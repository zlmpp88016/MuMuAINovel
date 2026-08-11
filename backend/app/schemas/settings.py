"""设置相关的Pydantic模型"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Dict, Optional
from datetime import datetime


class SettingsBase(BaseModel):
    """设置基础模型"""
    model_config = ConfigDict(protected_namespaces=())
    
    api_provider: Optional[str] = Field(default="openai", description="API提供商")
    api_key: Optional[str] = Field(default=None, description="API密钥")
    api_base_url: Optional[str] = Field(default=None, description="自定义API地址")
    llm_model: Optional[str] = Field(default="gpt-4", description="模型名称")
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
    max_tokens: Optional[int] = Field(default=2000, ge=1, description="最大token数")
    preferences: Optional[str] = Field(default=None, description="其他偏好设置(JSON)")


class SettingsCreate(SettingsBase):
    """创建设置请求模型"""
    pass


class SettingsUpdate(SettingsBase):
    """更新设置请求模型"""
    pass


class SettingsResponse(SettingsBase):
    """设置响应模型"""
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class LLMConfigurationCreate(BaseModel):
    """Create a reusable LLM API configuration."""

    name: str = Field(..., min_length=1, max_length=100)
    api_provider: str = Field("openai", min_length=1, max_length=50)
    api_key: str = Field(..., min_length=1, max_length=500)
    api_base_url: Optional[str] = Field(None, max_length=500)
    llm_model: str = Field(..., min_length=1, max_length=150)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(2000, ge=1)
    enabled: bool = True
    is_default: bool = False


class LLMConfigurationUpdate(BaseModel):
    """Update a reusable LLM API configuration."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    api_provider: Optional[str] = Field(None, min_length=1, max_length=50)
    api_key: Optional[str] = Field(None, min_length=1, max_length=500)
    api_base_url: Optional[str] = Field(None, max_length=500)
    llm_model: Optional[str] = Field(None, min_length=1, max_length=150)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None


class LLMConfigurationResponse(BaseModel):
    """Safe LLM configuration response without the plaintext API key."""

    id: str
    user_id: str
    name: str
    api_provider: str
    api_base_url: Optional[str] = None
    llm_model: str
    temperature: float
    max_tokens: int
    enabled: bool
    is_default: bool
    api_key_masked: str
    api_key_configured: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LLMModuleBindingUpdate(BaseModel):
    """Replace module bindings; use null to clear a module."""

    bindings: Dict[str, Optional[str]] = Field(default_factory=dict)


class LLMModuleBindingResponse(BaseModel):
    """A module binding with its safe configuration summary."""

    module_key: str
    llm_config_id: str
    config: LLMConfigurationResponse


class LLMConfigurationConnectionRequest(BaseModel):
    """Connection details used for model discovery or an API test."""

    api_provider: str = Field("openai", min_length=1, max_length=50)
    api_key: str = Field(..., min_length=1, max_length=500)
    api_base_url: Optional[str] = Field(None, max_length=500)
    llm_model: str = Field(..., min_length=1, max_length=150)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(100, ge=1)
