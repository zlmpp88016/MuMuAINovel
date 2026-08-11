"""设置管理 API。"""
from fastapi import APIRouter, HTTPException, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update
from typing import Dict, Any, List
from pydantic import BaseModel
import httpx

from app.database import get_db
from app.models.settings import Settings
from app.models.llm_configuration import LLMConfiguration, LLMModuleBinding
from app.schemas.settings import (
    SettingsCreate,
    SettingsUpdate,
    SettingsResponse,
    LLMConfigurationCreate,
    LLMConfigurationUpdate,
    LLMConfigurationResponse,
    LLMModuleBindingUpdate,
    LLMConfigurationConnectionRequest,
)
from app.user_manager import User
from app.logger import get_logger
from app.config import settings as app_settings
from app.services.ai_service import (
    AIService,
    create_user_ai_service,
    is_openai_compatible_provider,
)
from app.services.llm_configuration_service import (
    MODULE_KEYS,
    MODULE_LABELS,
    choose_config_id,
    mask_api_key,
    module_key_from_path,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["设置管理"])


def read_env_defaults() -> Dict[str, Any]:
    """从.env文件读取默认配置（仅读取，不修改）"""
    provider = (app_settings.default_ai_provider or "openai").strip().lower()
    if provider == "anthropic":
        api_key = app_settings.anthropic_api_key or ""
        api_base_url = app_settings.anthropic_base_url or ""
    else:
        api_key = app_settings.openai_api_key or ""
        api_base_url = app_settings.openai_base_url or ""

    return {
        "api_provider": provider,
        "api_key": api_key,
        "api_base_url": api_base_url,
        "llm_model": app_settings.default_model,
        "temperature": app_settings.default_temperature,
        "max_tokens": app_settings.default_max_tokens,
    }


def require_login(request: Request):
    """依赖：要求用户已登录"""
    if not hasattr(request.state, "user") or not request.state.user:
        raise HTTPException(status_code=401, detail="需要登录")
    return request.state.user


def _validate_provider(provider: str) -> str:
    """Normalize and validate providers supported by ``AIService``."""

    normalized = (provider or "").strip().lower()
    if normalized != "anthropic" and not is_openai_compatible_provider(normalized):
        raise HTTPException(
            status_code=422,
            detail=(
                f"不支持的 AI 提供商: {provider}。"
                "请使用 OpenAI 兼容提供商、custom 或 anthropic。"
            ),
        )
    return normalized


def _configuration_response(config: LLMConfiguration) -> LLMConfigurationResponse:
    """Build a response that never includes the plaintext API key."""

    return LLMConfigurationResponse(
        id=config.id,
        user_id=config.user_id,
        name=config.name,
        api_provider=config.api_provider,
        api_base_url=config.api_base_url or None,
        llm_model=config.llm_model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        enabled=config.enabled,
        is_default=config.is_default,
        api_key_masked=mask_api_key(config.api_key),
        api_key_configured=bool(config.api_key),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _legacy_settings_response(settings: Settings) -> SettingsResponse:
    """Serialize legacy settings without returning its plaintext API key."""

    response = SettingsResponse.model_validate(settings)
    return response.model_copy(update={"api_key": mask_api_key(settings.api_key)})


def _preserve_masked_legacy_key(
    settings: Settings | None,
    values: Dict[str, Any],
) -> None:
    """Treat an unchanged masked key as "keep the existing secret"."""

    if not settings or "api_key" not in values:
        return
    if values["api_key"] == mask_api_key(settings.api_key):
        values.pop("api_key")


async def _get_owned_configuration(
    db: AsyncSession,
    user_id: str,
    config_id: str,
    *,
    enabled_only: bool = False,
) -> LLMConfiguration:
    """Load a configuration and enforce user ownership."""

    normalized_id = (config_id or "").strip()
    if not normalized_id:
        raise HTTPException(status_code=404, detail="LLM 配置不存在")

    query = select(LLMConfiguration).where(
        LLMConfiguration.id == normalized_id,
        LLMConfiguration.user_id == user_id,
    )
    if enabled_only:
        query = query.where(LLMConfiguration.enabled.is_(True))

    result = await db.execute(query)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="LLM 配置不存在或已禁用")
    return config


async def _find_enabled_configuration(
    db: AsyncSession,
    user_id: str,
    config_id: str,
) -> LLMConfiguration | None:
    """Load an enabled configuration for fallback resolution."""

    normalized_id = (config_id or "").strip()
    if not normalized_id:
        return None

    result = await db.execute(
        select(LLMConfiguration).where(
            LLMConfiguration.id == normalized_id,
            LLMConfiguration.user_id == user_id,
            LLMConfiguration.enabled.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _get_default_configuration(
    db: AsyncSession,
    user_id: str,
) -> LLMConfiguration | None:
    """Return the user's enabled default configuration, if one exists."""

    result = await db.execute(
        select(LLMConfiguration)
        .where(
            LLMConfiguration.user_id == user_id,
            LLMConfiguration.enabled.is_(True),
            LLMConfiguration.is_default.is_(True),
        )
        .order_by(LLMConfiguration.updated_at.desc(), LLMConfiguration.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _read_request_configuration_id(request: Request) -> str | None:
    """Read ``llm_config_id`` without consuming the route request body."""

    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return None

    try:
        payload = await request.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    value = payload.get("llm_config_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _get_or_create_legacy_settings(
    db: AsyncSession,
    user_id: str,
) -> Settings:
    """Load the legacy single-configuration row, creating env defaults if needed."""

    result = await db.execute(select(Settings).where(Settings.user_id == user_id))
    settings = result.scalar_one_or_none()
    if settings:
        return settings

    settings = Settings(user_id=user_id, **read_env_defaults())
    db.add(settings)
    await db.commit()
    await db.refresh(settings)
    logger.info(f"用户 {user_id} 首次使用 AI 服务，已从 env 同步旧配置到数据库")
    return settings


async def get_user_ai_service(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db)
) -> AIService:
    """
    依赖：获取当前用户的AI服务实例
    从数据库读取用户设置并创建对应的AI服务
    """
    request_config_id = await _read_request_configuration_id(request)
    module_key = module_key_from_path(request.url.path)
    module_config_id = None

    if module_key:
        binding_result = await db.execute(
            select(LLMModuleBinding).where(
                LLMModuleBinding.user_id == user.user_id,
                LLMModuleBinding.module_key == module_key,
            )
        )
        binding = binding_result.scalar_one_or_none()
        module_config_id = binding.llm_config_id if binding else None

    default_config = None
    if not request_config_id:
        default_config = await _get_default_configuration(db, user.user_id)

    selected_config = None
    selected_config_id = choose_config_id(
        request_config_id,
        module_config_id,
        default_config.id if default_config else None,
    )
    if request_config_id:
        # An explicit request override must fail loudly when it is invalid.
        selected_config = await _get_owned_configuration(
            db,
            user.user_id,
            request_config_id,
            enabled_only=True,
        )
    elif module_config_id and selected_config_id == module_config_id:
        # A stale or disabled binding is treated as a fallback miss.
        selected_config = await _find_enabled_configuration(
            db,
            user.user_id,
            module_config_id,
        )

    if not selected_config:
        selected_config = default_config

    if selected_config:
        logger.debug(
            "用户 %s 使用 LLM 配置 %s（模块=%s，请求覆盖=%s）",
            user.user_id,
            selected_config.id,
            module_key or "none",
            bool(request_config_id),
        )
        return create_user_ai_service(
            api_provider=selected_config.api_provider,
            api_key=selected_config.api_key,
            api_base_url=selected_config.api_base_url or None,
            model_name=selected_config.llm_model,
            temperature=selected_config.temperature,
            max_tokens=selected_config.max_tokens,
        )

    # Preserve the legacy single-configuration fallback.
    settings = await _get_or_create_legacy_settings(db, user.user_id)
    return create_user_ai_service(
        api_provider=settings.api_provider,
        api_key=settings.api_key,
        api_base_url=settings.api_base_url or None,
        model_name=settings.llm_model,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens
    )


@router.get("", response_model=SettingsResponse)
async def get_settings(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db)
):
    """
    获取当前用户的设置
    如果用户没有保存过设置，自动从.env创建并保存到数据库
    """
    result = await db.execute(
        select(Settings).where(Settings.user_id == user.user_id)
    )
    settings = result.scalar_one_or_none()
    
    if not settings:
        # 如果用户没有保存过设置，从.env读取默认配置并保存到数据库
        env_defaults = read_env_defaults()
        logger.info(f"用户 {user.user_id} 首次获取设置，自动从.env同步到数据库")
        
        # 创建新设置并保存到数据库
        settings = Settings(
            user_id=user.user_id,
            **env_defaults
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        logger.info(f"用户 {user.user_id} 的设置已从.env同步到数据库")
    
    logger.info(f"用户 {user.user_id} 获取已保存的设置")
    return _legacy_settings_response(settings)


@router.post("", response_model=SettingsResponse)
async def save_settings(
    data: SettingsCreate,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db)
):
    """
    创建或更新当前用户的设置（Upsert）
    如果设置已存在则更新，否则创建新设置
    仅保存到数据库
    """
    # 查找现有设置
    result = await db.execute(
        select(Settings).where(Settings.user_id == user.user_id)
    )
    settings = result.scalar_one_or_none()
    
    # 准备数据
    settings_dict = data.model_dump(exclude_unset=True)
    _preserve_masked_legacy_key(settings, settings_dict)
    
    if settings:
        # 更新现有设置
        for key, value in settings_dict.items():
            setattr(settings, key, value)
        
        await db.commit()
        await db.refresh(settings)
        logger.info(f"用户 {user.user_id} 更新设置")
    else:
        # 创建新设置
        settings = Settings(
            user_id=user.user_id,
            **settings_dict
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
        logger.info(f"用户 {user.user_id} 创建设置")
    
    return _legacy_settings_response(settings)


@router.put("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db)
):
    """
    更新当前用户的设置
    仅保存到数据库
    """
    result = await db.execute(
        select(Settings).where(Settings.user_id == user.user_id)
    )
    settings = result.scalar_one_or_none()
    
    if not settings:
        raise HTTPException(status_code=404, detail="设置不存在，请先创建设置")
    
    # 更新设置
    update_data = data.model_dump(exclude_unset=True)
    _preserve_masked_legacy_key(settings, update_data)
    for key, value in update_data.items():
        setattr(settings, key, value)
    
    await db.commit()
    await db.refresh(settings)
    logger.info(f"用户 {user.user_id} 更新设置")
    
    return _legacy_settings_response(settings)


@router.delete("")
async def delete_settings(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db)
):
    """
    删除当前用户的设置
    """
    result = await db.execute(
        select(Settings).where(Settings.user_id == user.user_id)
    )
    settings = result.scalar_one_or_none()
    
    if not settings:
        raise HTTPException(status_code=404, detail="设置不存在")
    
    await db.delete(settings)
    await db.commit()
    logger.info(f"用户 {user.user_id} 删除设置")
    
    return {"message": "设置已删除", "user_id": user.user_id}


async def _clear_other_defaults(
    db: AsyncSession,
    user_id: str,
    excluded_config_id: str | None = None,
) -> None:
    """Keep at most one default configuration for a user."""

    query = update(LLMConfiguration).where(
        LLMConfiguration.user_id == user_id,
        LLMConfiguration.is_default.is_(True),
    )
    if excluded_config_id:
        query = query.where(LLMConfiguration.id != excluded_config_id)
    await db.execute(query.values(is_default=False))


async def _get_bindings_payload(
    db: AsyncSession,
    user_id: str,
) -> Dict[str, Any]:
    """Return module metadata and safe binding summaries."""

    result = await db.execute(
        select(LLMModuleBinding, LLMConfiguration)
        .join(LLMConfiguration, LLMConfiguration.id == LLMModuleBinding.llm_config_id)
        .where(LLMModuleBinding.user_id == user_id)
        .order_by(LLMModuleBinding.module_key)
    )
    bindings = []
    for binding, config in result.all():
        bindings.append(
            {
                "module_key": binding.module_key,
                "llm_config_id": binding.llm_config_id,
                "config": _configuration_response(config),
            }
        )

    return {
        "modules": [
            {"key": key, "label": MODULE_LABELS.get(key, key)}
            for key in sorted(MODULE_KEYS)
        ],
        "bindings": bindings,
    }


@router.get("/llm-configurations", response_model=List[LLMConfigurationResponse])
async def list_llm_configurations(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's reusable LLM configurations."""

    result = await db.execute(
        select(LLMConfiguration)
        .where(LLMConfiguration.user_id == user.user_id)
        .order_by(
            LLMConfiguration.is_default.desc(),
            LLMConfiguration.enabled.desc(),
            LLMConfiguration.created_at.desc(),
        )
    )
    return [_configuration_response(config) for config in result.scalars().all()]


@router.post("/llm-configurations", response_model=LLMConfigurationResponse, status_code=201)
async def create_llm_configuration(
    data: LLMConfigurationCreate,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Create a reusable LLM API configuration."""

    provider = _validate_provider(data.api_provider)
    name = data.name.strip()
    api_key = data.api_key.strip()
    llm_model = data.llm_model.strip()
    if not name or not api_key or not llm_model:
        raise HTTPException(status_code=422, detail="配置名称、API Key 和模型名称不能为空")
    if data.is_default:
        await _clear_other_defaults(db, user.user_id)

    config = LLMConfiguration(
        user_id=user.user_id,
        name=name,
        api_provider=provider,
        api_key=api_key,
        api_base_url=data.api_base_url or "",
        llm_model=llm_model,
        temperature=data.temperature,
        max_tokens=data.max_tokens,
        enabled=data.enabled,
        is_default=data.is_default and data.enabled,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    logger.info("用户 %s 创建 LLM 配置 %s", user.user_id, config.id)
    return _configuration_response(config)


@router.put(
    "/llm-configurations/{config_id}",
    response_model=LLMConfigurationResponse,
)
async def update_llm_configuration(
    config_id: str,
    data: LLMConfigurationUpdate,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Update a reusable LLM API configuration without returning its secret."""

    config = await _get_owned_configuration(db, user.user_id, config_id)
    updates = data.model_dump(exclude_unset=True)

    if "api_provider" in updates:
        updates["api_provider"] = _validate_provider(updates["api_provider"])
    if "name" in updates:
        if updates["name"] is None:
            raise HTTPException(status_code=422, detail="配置名称不能为空")
        updates["name"] = updates["name"].strip()
    if "llm_model" in updates:
        if updates["llm_model"] is None:
            raise HTTPException(status_code=422, detail="模型名称不能为空")
        updates["llm_model"] = updates["llm_model"].strip()
    if "api_base_url" in updates:
        updates["api_base_url"] = updates["api_base_url"] or ""
    if "api_key" in updates:
        if updates["api_key"] is None:
            updates.pop("api_key")
        else:
            updates["api_key"] = updates["api_key"].strip()
    if any(field in updates and not updates[field] for field in ("name", "api_key", "llm_model")):
        raise HTTPException(status_code=422, detail="配置名称、API Key 和模型名称不能为空")
    if updates.get("enabled") is False:
        updates["is_default"] = False
    if updates.get("is_default") is True:
        await _clear_other_defaults(db, user.user_id, config.id)

    for field, value in updates.items():
        setattr(config, field, value)

    await db.commit()
    await db.refresh(config)
    logger.info("用户 %s 更新 LLM 配置 %s", user.user_id, config.id)
    return _configuration_response(config)


@router.delete("/llm-configurations/{config_id}")
async def delete_llm_configuration(
    config_id: str,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Delete a reusable LLM configuration and its module bindings."""

    config = await _get_owned_configuration(db, user.user_id, config_id)
    await db.execute(
        delete(LLMModuleBinding).where(
            LLMModuleBinding.user_id == user.user_id,
            LLMModuleBinding.llm_config_id == config.id,
        )
    )
    await db.delete(config)
    await db.commit()
    logger.info("用户 %s 删除 LLM 配置 %s", user.user_id, config.id)
    return {"message": "LLM 配置已删除", "id": config.id}


@router.get("/llm-bindings")
async def get_llm_bindings(
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Get module metadata and the current user's configured bindings."""

    return await _get_bindings_payload(db, user.user_id)


@router.put("/llm-bindings")
async def update_llm_bindings(
    data: LLMModuleBindingUpdate,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Set or clear LLM selections for one or more generation modules."""

    for module_key, config_id in data.bindings.items():
        if module_key not in MODULE_KEYS:
            raise HTTPException(status_code=422, detail=f"不支持的小说模块: {module_key}")

        existing_result = await db.execute(
            select(LLMModuleBinding).where(
                LLMModuleBinding.user_id == user.user_id,
                LLMModuleBinding.module_key == module_key,
            )
        )
        existing = existing_result.scalar_one_or_none()
        normalized_config_id = (config_id or "").strip()

        if not normalized_config_id:
            if existing:
                await db.delete(existing)
            continue

        await _get_owned_configuration(
            db,
            user.user_id,
            normalized_config_id,
            enabled_only=True,
        )
        if existing:
            existing.llm_config_id = normalized_config_id
        else:
            db.add(
                LLMModuleBinding(
                    user_id=user.user_id,
                    module_key=module_key,
                    llm_config_id=normalized_config_id,
                )
            )

    await db.commit()
    return await _get_bindings_payload(db, user.user_id)


@router.post("/llm-configurations/test")
async def test_llm_configuration_connection(
    data: LLMConfigurationConnectionRequest,
    user: User = Depends(require_login),
):
    """Test an unsaved LLM configuration using the existing test contract."""

    return await test_api_connection(
        ApiTestRequest(
            api_key=data.api_key,
            api_base_url=data.api_base_url or "",
            provider=data.api_provider,
            llm_model=data.llm_model,
        ),
        user=user,
    )


@router.post("/llm-configurations/{config_id}/test")
async def test_saved_llm_configuration(
    config_id: str,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Test a saved configuration without exposing its API key."""

    config = await _get_owned_configuration(db, user.user_id, config_id, enabled_only=True)
    return await test_api_connection(
        ApiTestRequest(
            api_key=config.api_key,
            api_base_url=config.api_base_url or "",
            provider=config.api_provider,
            llm_model=config.llm_model,
        ),
        user=user,
    )


@router.get("/models")
async def get_available_models(
    api_key: str | None = None,
    api_base_url: str | None = None,
    provider: str = "openai",
    config_id: str | None = None,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """
    从配置的 API 获取可用的模型列表
    
    参数：
        api_key: API 密钥
        api_base_url: API 基础 URL
        provider: API 提供商 (openai, anthropic, azure, custom)
    
    返回：
        模型列表
    """
    if config_id:
        config = await _get_owned_configuration(db, user.user_id, config_id, enabled_only=True)
        api_key = config.api_key
        api_base_url = config.api_base_url
        provider = config.api_provider

    if not api_key or not api_base_url:
        raise HTTPException(status_code=422, detail="请提供 API Key 和 API Base URL")

    provider = _validate_provider(provider)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if is_openai_compatible_provider(provider):
                # OpenAI 兼容接口获取模型列表
                url = f"{api_base_url.rstrip('/')}/models"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                logger.info(f"正在从 {url} 获取模型列表")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                models = []
                
                if "data" in data and isinstance(data["data"], list):
                    for model in data["data"]:
                        model_id = model.get("id", "")
                        # 返回所有模型，不进行过滤
                        if model_id:
                            models.append({
                                "value": model_id,
                                "label": model_id,
                                "description": model.get("description", "") or f"Created: {model.get('created', 'N/A')}"
                            })
                
                if not models:
                    raise HTTPException(
                        status_code=404,
                        detail="未能从 API 获取到可用的模型列表"
                    )
                
                logger.info(f"成功获取 {len(models)} 个模型")
                return {
                    "provider": provider,
                    "models": models,
                    "count": len(models)
                }
                
            elif provider == "anthropic":
                # Anthropic 没有公开的模型列表API
                raise HTTPException(
                    status_code=400,
                    detail="Anthropic 不支持自动获取模型列表，请手动输入模型名称"
                )
            
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的提供商: {provider}"
                )
            
    except httpx.HTTPStatusError as e:
        logger.error(f"获取模型列表失败 (HTTP {e.response.status_code}): {e.response.text}")
        raise HTTPException(
            status_code=400,
            detail=f"无法从 API 获取模型列表 (HTTP {e.response.status_code})"
        )
    except httpx.RequestError as e:
        logger.error(f"请求模型列表失败: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=f"无法连接到 API: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取模型列表时发生错误: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"获取模型列表失败: {str(e)}"
        )


class ApiTestRequest(BaseModel):
    """API 测试请求模型"""
    api_key: str
    api_base_url: str
    provider: str
    llm_model: str


@router.post("/test")
async def test_api_connection(
    data: ApiTestRequest,
    user: User = Depends(require_login),
):
    """
    测试 API 连接和配置是否正确
    
    参数：
        data: 包含 API 配置的请求数据
    
    返回：
        测试结果包含状态、响应时间和详细信息
    """
    api_key = data.api_key
    api_base_url = data.api_base_url
    provider = data.provider
    llm_model = data.llm_model
    import time
    
    try:
        start_time = time.time()
        
        # 创建临时 AI 服务实例
        test_service = AIService(
            api_provider=provider,
            api_key=api_key,
            api_base_url=api_base_url,
            default_model=llm_model,
            default_temperature=0.7,
            default_max_tokens=100
        )
        
        # 发送简单的测试请求
        test_prompt = "请用一句话回复：测试成功"
        
        logger.info(f"🧪 开始测试 API 连接")
        logger.info(f"  - 提供商: {provider}")
        logger.info(f"  - 模型: {llm_model}")
        logger.info(f"  - 基础地址（Base URL）: {api_base_url}")
        
        response = await test_service.generate_text(
            prompt=test_prompt,
            provider=provider,
            model=llm_model,
            temperature=0.7,
            max_tokens=8000
        )
        
        end_time = time.time()
        response_time = round((end_time - start_time) * 1000, 2)  # 转换为毫秒
        
        logger.info(f"✅ API 测试成功")
        logger.info(f"  - 响应时间: {response_time}ms")
        
        # 安全地处理响应内容（确保是字符串）
        response_str = str(response) if response else 'N/A'
        logger.info(f"  - 响应内容: {response_str[:100]}")
        
        return {
            "success": True,
            "message": "API 连接测试成功",
            "response_time_ms": response_time,
            "provider": provider,
            "model": llm_model,
            "response_preview": response_str[:100] if len(response_str) > 100 else response_str,
            "details": {
                "api_available": True,
                "model_accessible": True,
                "response_valid": bool(response)
            }
        }
        
    except ValueError as e:
        # 配置错误
        error_msg = str(e)
        logger.error(f"❌ API 配置错误: {error_msg}")
        return {
            "success": False,
            "message": "API 配置错误",
            "error": error_msg,
            "error_type": "ConfigurationError",
            "suggestions": [
                "请检查 API Key 是否正确",
                "请确认 API Base URL 格式正确",
                "请验证所选提供商是否匹配"
            ]
        }
        
    except TimeoutError as e:
        # 超时错误
        error_msg = str(e)
        logger.error(f"❌ API 请求超时: {error_msg}")
        return {
            "success": False,
            "message": "API 请求超时",
            "error": error_msg,
            "error_type": "TimeoutError",
            "suggestions": [
                "请检查网络连接",
                "请确认 API Base URL 是否可访问",
                "如果使用代理，请检查代理设置"
            ]
        }
        
    except Exception as e:
        # 其他错误
        error_msg = str(e)
        error_type = type(e).__name__
        
        logger.error(f"❌ API 测试失败: {error_msg}")
        logger.error(f"  - 错误类型: {error_type}")
        
        # 分析错误原因并提供建议
        suggestions = []
        if "blocked" in error_msg.lower():
            suggestions = [
                "请求被 API 提供商阻止",
                "可能原因：API Key 被限制或地区限制",
                "建议：检查 API Key 状态和账户余额",
                "建议：尝试更换 API Base URL 或使用代理"
            ]
        elif "unauthorized" in error_msg.lower() or "401" in error_msg:
            suggestions = [
                "API Key 认证失败",
                "建议：检查 API Key 是否正确",
                "建议：确认 API Key 是否过期"
            ]
        elif "not found" in error_msg.lower() or "404" in error_msg:
            suggestions = [
                "API 端点不存在或模型不可用",
                "建议：检查 API Base URL 是否正确",
                "建议：确认模型名称是否正确"
            ]
        elif "rate limit" in error_msg.lower() or "429" in error_msg:
            suggestions = [
                "API 请求频率超限",
                "建议：稍后重试",
                "建议：升级 API 套餐"
            ]
        elif "insufficient" in error_msg.lower() or "quota" in error_msg.lower():
            suggestions = [
                "API 配额不足",
                "建议：检查账户余额",
                "建议：充值或升级套餐"
            ]
        else:
            suggestions = [
                "请检查所有配置参数是否正确",
                "请确认网络连接正常",
                "请查看详细错误信息"
            ]
        
        return {
            "success": False,
            "message": "API 测试失败",
            "error": error_msg,
            "error_type": error_type,
            "suggestions": suggestions
        }
