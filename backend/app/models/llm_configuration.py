"""Per-user LLM configurations and module bindings."""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class LLMConfiguration(Base):
    """A reusable LLM API configuration owned by one user."""

    __tablename__ = "llm_configurations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    api_provider = Column(String(50), nullable=False, default="openai")
    api_key = Column(String(500), nullable=False)
    api_base_url = Column(String(500), nullable=False)
    llm_model = Column(String(150), nullable=False)
    temperature = Column(Float, nullable=False, default=0.7)
    max_tokens = Column(Integer, nullable=False, default=2000)
    enabled = Column(Boolean, nullable=False, default=True)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_llm_config_user_enabled", "user_id", "enabled"),
        Index("idx_llm_config_user_default", "user_id", "is_default"),
    )

    def __repr__(self) -> str:
        return f"<LLMConfiguration(id={self.id}, user_id={self.user_id}, name={self.name})>"


class LLMModuleBinding(Base):
    """The selected LLM configuration for a user's module."""

    __tablename__ = "llm_module_bindings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), nullable=False, index=True)
    module_key = Column(String(50), nullable=False)
    llm_config_id = Column(
        String(36),
        ForeignKey("llm_configurations.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "module_key", name="uq_llm_module_binding_user_module"),
        Index("idx_llm_module_binding_config", "llm_config_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<LLMModuleBinding(user_id={self.user_id}, module_key={self.module_key}, "
            f"llm_config_id={self.llm_config_id})>"
        )
