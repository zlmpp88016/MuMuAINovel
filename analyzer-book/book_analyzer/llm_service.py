"""LLM 调用封装。

这个模块借鉴主后端的 provider/base_url/model 配置方式，但保持运行时独立。
同一个类既可用于主分析模型，也可通过显式参数创建段落打标签的小模型客户端。
"""

from __future__ import annotations

import asyncio
from typing import Any
import logging

import httpx

from book_analyzer.config import Settings


logger = logging.getLogger(__name__)

try:
    from anthropic import AsyncAnthropic, APITimeoutError, APIStatusError, InternalServerError
except ImportError:  # pragma: no cover - 取决于环境
    AsyncAnthropic = None
    APITimeoutError = None  # type: ignore[assignment]
    APIStatusError = None  # type: ignore[assignment]
    InternalServerError = None  # type: ignore[assignment]


class AIService:
    """统一的文本生成服务。

    支持 OpenAI-compatible HTTP 接口和 Anthropic SDK。构造时传入的显式
    参数会覆盖 ``Settings`` 中的默认值，用于创建独立的 tagging 模型实例。
    """

    def __init__(
        self,
        settings: Settings,
        api_provider: str | None = None,
        api_key: str | None = None,
        api_base_url: str | None = None,
        default_model: str | None = None,
        default_temperature: float | None = None,
        default_max_tokens: int | None = None,
    ) -> None:
        """初始化模型客户端和默认生成参数。

        Args:
            settings: book-analyzer 的独立配置对象。
            api_provider: 可选模型提供方，支持 ``openai`` 或 ``anthropic``。
            api_key: 可选 API key；为空时按 provider 回退到全局配置。
            api_base_url: 可选 API base URL，常用于 OpenAI-compatible 小模型服务。
            default_model: 默认模型名。
            default_temperature: 默认采样温度。
            default_max_tokens: 默认最大输出 token 数。
        """
        self.settings = settings
        self.api_provider = api_provider or settings.default_ai_provider
        self.default_model = default_model or settings.default_model
        self.default_temperature = (
            default_temperature
            if default_temperature is not None
            else settings.default_temperature
        )
        self.default_max_tokens = default_max_tokens or settings.default_max_tokens
        self.openai_api_key = (
            (api_key or settings.openai_api_key)
            if self.api_provider == "openai"
            else settings.openai_api_key
        )
        self.openai_base_url = (
            (api_base_url or settings.openai_base_url)
            if self.api_provider == "openai"
            else settings.openai_base_url
        ) or "https://api.openai.com/v1"
        self.openai_base_url = self.openai_base_url.rstrip("/")
        self.anthropic_api_key = (
            (api_key or settings.anthropic_api_key)
            if self.api_provider == "anthropic"
            else settings.anthropic_api_key
        )
        self.anthropic_base_url = (
            (api_base_url or settings.anthropic_base_url)
            if self.api_provider == "anthropic"
            else settings.anthropic_base_url
        )

        self.openai_http_client = None
        self.anthropic_client = None

        if self.openai_api_key:
            limits = httpx.Limits(
                max_keepalive_connections=50,
                max_connections=100,
                keepalive_expiry=30.0,
            )
            self.openai_http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=60.0, read=180.0, write=60.0, pool=60.0),
                limits=limits,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )

        if self.anthropic_api_key and AsyncAnthropic is not None:
            client_kwargs: dict[str, Any] = {"api_key": self.anthropic_api_key}
            if self.anthropic_base_url:
                client_kwargs["base_url"] = self.anthropic_base_url
            self.anthropic_client = AsyncAnthropic(**client_kwargs)

    def is_available(self, provider: str | None = None) -> bool:
        """检查指定 provider 是否已具备发起请求的必要配置。

        Args:
            provider: 可选 provider；为空时检查当前实例的 provider。

        Returns:
            ``True`` 表示客户端和 key 都已准备好。
        """
        resolved_provider = provider or self.api_provider
        if resolved_provider == "openai":
            return self.openai_http_client is not None and bool(self.openai_api_key)
        if resolved_provider == "anthropic":
            return self.anthropic_client is not None and bool(self.anthropic_api_key)
        return False

    async def generate_text(
        self,
        prompt: str,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """使用配置好的 provider 生成文本。

        Args:
            prompt: 用户提示词。
            provider: 单次调用覆盖 provider。
            model: 单次调用覆盖模型名。
            temperature: 单次调用覆盖采样温度。
            max_tokens: 单次调用覆盖最大输出 token 数。
            system_prompt: 可选系统提示词。

        Returns:
            包含 ``content`` 和 ``finish_reason`` 的统一响应字典。

        Raises:
            ValueError: provider 不支持或底层客户端未初始化。
        """
        resolved_provider = provider or self.api_provider
        resolved_model = model or self.default_model
        resolved_temperature = (
            temperature if temperature is not None else self.default_temperature
        )
        resolved_max_tokens = max_tokens or self.default_max_tokens

        logger.info("LLM 调用: provider=%s, model=%s, max_tokens=%d", resolved_provider, resolved_model, resolved_max_tokens)

        try:
            if resolved_provider == "openai":
                content = await self._generate_openai(
                    prompt=prompt,
                    model=resolved_model,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    system_prompt=system_prompt,
                )
                return {"content": content, "finish_reason": "stop"}
            if resolved_provider == "anthropic":
                content = await self._generate_anthropic(
                    prompt=prompt,
                    model=resolved_model,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    system_prompt=system_prompt,
                )
                return {"content": content, "finish_reason": "stop"}
            raise ValueError(f"不支持的 AI 提供商: {resolved_provider}")
        except Exception:
            logger.exception("LLM 调用异常: provider=%s, model=%s", resolved_provider, resolved_model)
            raise

    async def close(self) -> None:
        """关闭底层异步 HTTP 客户端。"""
        if self.openai_http_client is not None:
            await self.openai_http_client.aclose()

    async def _generate_openai(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: str | None,
    ) -> str:
        """调用 OpenAI-compatible chat completions 接口。

        Args:
            prompt: 用户提示词。
            model: 模型名称。
            temperature: 采样温度。
            max_tokens: 最大输出 token 数。
            system_prompt: 可选系统提示词。

        Returns:
            模型返回的文本内容。
        """
        if not self.openai_http_client:
            raise ValueError("OpenAI 客户端未初始化，请检查 API key 配置")

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        logger.info("OpenAI API 请求: model=%s, messages=%d, max_tokens=%d",
                     model, len(messages), max_tokens)
        headers = {
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
        }
        json_body = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                # "max_tokens": max_tokens,
            }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self.openai_http_client.post(
                    f"{self.openai_base_url}/chat/completions",
                    headers=headers,
                    json=json_body,
                )
                response.raise_for_status()
                payload = response.json()
                choices = payload.get("choices", [])
                if not choices:
                    raise ValueError("OpenAI 返回的 choices 为空")
                message = choices[0].get("message", {})
                content = message.get("content", "")
                if not content:
                    logger.error(
                        "OpenAI 返回了空内容 url:%s/chat/completions, response:%s",
                        self.openai_base_url, payload,
                    )
                    raise ValueError("OpenAI 返回了空内容")
                finish = choices[0].get("finish_reason", "unknown")
                logger.info("OpenAI API 响应成功: model=%s, finish_reason=%s, 内容长度=%d",
                             model, finish, len(content))
                return content
            except (httpx.ReadTimeout, httpx.ConnectError):
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    "OpenAI API 连接异常(超时/连不上)，第 %d/%d 次重试，等待 %.1fs ...",
                    attempt + 1, max_retries, 2 ** attempt,
                )
                await asyncio.sleep(2 ** attempt)
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    "OpenAI API 返回 %d，第 %d/%d 次重试，等待 %.1fs ...",
                    e.response.status_code, attempt + 1, max_retries, 2 ** attempt,
                )
                await asyncio.sleep(2 ** attempt)

    async def _generate_anthropic(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        system_prompt: str | None,
    ) -> str:
        """调用 Anthropic messages 接口。

        Args:
            prompt: 用户提示词。
            model: 模型名称。
            temperature: 采样温度。
            max_tokens: 最大输出 token 数。
            system_prompt: 可选系统提示词。

        Returns:
            合并后的文本块内容。
        """
        if not self.anthropic_client:
            raise ValueError("Anthropic 客户端未初始化，请检查 API key 配置")

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        logger.info("Anthropic API 请求: model=%s, max_tokens=%d", model, max_tokens)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self.anthropic_client.messages.create(**kwargs)
                content = ""
                for block in response.content:
                    if block.type == "text":
                        content += block.text
                if not content:
                    raise ValueError("Anthropic 返回了空内容")
                logger.info(
                    "Anthropic API 响应成功: model=%s, 内容长度=%d, stop_reason=%s",
                    model, len(content), response.stop_reason,
                )
                return content
            except httpx.ReadTimeout:
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    "Anthropic API 超时，第 %d/%d 次重试，等待 %.1fs ...",
                    attempt + 1, max_retries, 2 ** attempt,
                )
                await asyncio.sleep(2 ** attempt)
            except APITimeoutError:  # type: ignore[misc]
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    "Anthropic API 超时，第 %d/%d 次重试，等待 %.1fs ...",
                    attempt + 1, max_retries, 2 ** attempt,
                )
                await asyncio.sleep(2 ** attempt)
            except InternalServerError:  # type: ignore[misc]
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    "Anthropic API 返回 500，第 %d/%d 次重试，等待 %.1fs ...",
                    attempt + 1, max_retries, 2 ** attempt,
                )
                await asyncio.sleep(2 ** attempt)
            except APIStatusError as e:  # type: ignore[misc]
                if e.status_code < 500:
                    raise
                if attempt == max_retries - 1:
                    raise
                logger.warning(
                    "Anthropic API 返回 %d，第 %d/%d 次重试，等待 %.1fs ...",
                    e.status_code, attempt + 1, max_retries, 2 ** attempt,
                )
                await asyncio.sleep(2 ** attempt)
