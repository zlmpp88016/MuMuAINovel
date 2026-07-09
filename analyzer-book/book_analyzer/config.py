"""独立 book-analyzer 服务配置。

Settings 只在 ``book-analyzer`` 内生效。本模块沿用主 backend 的
provider/base-url/model 配置风格，但不导入 backend 运行时配置，
保证这个服务可以独立部署。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


def _project_root() -> Path:
    """返回独立 ``book-analyzer`` 包的根目录。"""
    return Path(__file__).resolve().parents[1]


def _path_from_env(name: str, default: Path, root: Path) -> Path:
    """读取路径类环境变量，并把相对路径解析到项目根目录下。"""
    raw_value = os.getenv(name)
    if not raw_value:
        return default
    path = Path(raw_value).expanduser()
    return path if path.is_absolute() else root / path


def _database_url_from_env(root: Path, data_dir: Path) -> str:
    """读取 database URL，并把相对 SQLite 路径解析到项目根目录下。"""
    value = os.getenv("BOOK_ANALYZER_DATABASE_URL")
    if not value:
        return f"sqlite:///{(data_dir / 'book_analyzer.db').as_posix()}"
    if value == "sqlite:///:memory:":
        return value
    if value.startswith("sqlite:///"):
        sqlite_path = Path(value.removeprefix("sqlite:///")).expanduser()
        if not sqlite_path.is_absolute():
            sqlite_path = root / sqlite_path
        return f"sqlite:///{sqlite_path.as_posix()}"
    return value


def _bool_from_env(name: str, default: bool = False) -> bool:
    """读取布尔环境变量。"""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    """存储、分析、打标签和向量检索的运行时配置。

    字段按子系统分组：存储路径、上传与切块限制、主分析模型、
    专用段落打标签模型，以及 embedding / vector-store 选项。
    """

    app_name: str = "Book Analyzer"
    project_root: Path = _project_root()
    database_url: str = ""
    data_dir: Path = Path()
    uploads_dir: Path = Path()
    exports_dir: Path = Path()
    chroma_dir: Path = Path()
    embedding_cache_dir: Path = Path()
    max_upload_bytes: int = 50 * 1024 * 1024
    chunk_size: int = 2000
    chunk_overlap: int = 200

    analysis_backend: str = "auto"
    disable_rule_analysis: bool = False
    vector_backend: str = "auto"

    default_ai_provider: str = "openai"
    default_model: str = "gpt-4o-mini"
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None

    tagging_backend: str = "auto"
    tagging_ai_provider: str = "openai"
    tagging_model: str = "gpt-4o-mini"
    tagging_temperature: float = 0.1
    tagging_max_tokens: int = 600
    tagging_api_key: str | None = None
    tagging_base_url: str | None = None

    embedding_provider: str = "local"
    embedding_dim: int = 384
    embedding_api_url: str | None = None
    embedding_api_key: str | None = None
    embedding_api_model: str = "Qwen/Qwen3-VL-Embedding-8B"
    local_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    fallback_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    chroma_collection_name: str = ""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    batch_import_concurrency: int = 2

    def __post_init__(self) -> None:
        """补齐依赖 ``project_root`` 或向量维度的派生默认值。"""
        if not self.data_dir:
            self.data_dir = self.project_root / "data"
        if not self.uploads_dir:
            self.uploads_dir = self.data_dir / "uploads"
        if not self.exports_dir:
            self.exports_dir = self.data_dir / "exports"
        if not self.chroma_dir:
            self.chroma_dir = self.data_dir / "chroma_corpus"
        if not self.embedding_cache_dir:
            self.embedding_cache_dir = self.project_root / "embedding"
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'book_analyzer.db').as_posix()}"
        if not self.chroma_collection_name:
            self.chroma_collection_name = f"ba_corpus_dim{self.embedding_dim}"

    @classmethod
    def from_env(cls) -> "Settings":
        """从 ``BOOK_ANALYZER_*`` 环境变量构建 Settings。

        返回：
            已通过 ``__post_init__`` 补齐文件系统默认值的 Settings 对象。
        """
        root = _project_root()
        load_dotenv(root / ".env", override=False)
        data_dir = _path_from_env("BOOK_ANALYZER_DATA_DIR", root / "data", root)
        return cls(
            project_root=root,
            database_url=_database_url_from_env(root, data_dir),
            data_dir=data_dir,
            uploads_dir=_path_from_env(
                "BOOK_ANALYZER_UPLOADS_DIR",
                data_dir / "uploads",
                root,
            ),
            exports_dir=_path_from_env(
                "BOOK_ANALYZER_EXPORTS_DIR",
                data_dir / "exports",
                root,
            ),
            chroma_dir=_path_from_env(
                "BOOK_ANALYZER_CHROMA_DIR",
                data_dir / "chroma_corpus",
                root,
            ),
            embedding_cache_dir=_path_from_env(
                "BOOK_ANALYZER_EMBEDDING_CACHE_DIR",
                root / "embedding",
                root,
            ),
            max_upload_bytes=int(
                os.getenv("BOOK_ANALYZER_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
            ),
            chunk_size=int(os.getenv("BOOK_ANALYZER_CHUNK_SIZE", "2000")),
            chunk_overlap=int(os.getenv("BOOK_ANALYZER_CHUNK_OVERLAP", "200")),
            analysis_backend=os.getenv("BOOK_ANALYZER_ANALYSIS_BACKEND", "auto"),
            disable_rule_analysis=_bool_from_env("BOOK_ANALYZER_DISABLE_RULE_ANALYSIS"),
            vector_backend=os.getenv("BOOK_ANALYZER_VECTOR_BACKEND", "auto"),
            default_ai_provider=os.getenv("BOOK_ANALYZER_DEFAULT_AI_PROVIDER", "openai"),
            default_model=os.getenv("BOOK_ANALYZER_DEFAULT_MODEL", "gpt-4o-mini"),
            default_temperature=float(
                os.getenv("BOOK_ANALYZER_DEFAULT_TEMPERATURE", "0.7")
            ),
            default_max_tokens=int(
                os.getenv("BOOK_ANALYZER_DEFAULT_MAX_TOKENS", "2000")
            ),
            openai_api_key=os.getenv("BOOK_ANALYZER_OPENAI_API_KEY"),
            openai_base_url=os.getenv("BOOK_ANALYZER_OPENAI_BASE_URL"),
            anthropic_api_key=os.getenv("BOOK_ANALYZER_ANTHROPIC_API_KEY"),
            anthropic_base_url=os.getenv("BOOK_ANALYZER_ANTHROPIC_BASE_URL"),
            tagging_backend=os.getenv("BOOK_ANALYZER_TAGGING_BACKEND", "auto"),
            tagging_ai_provider=os.getenv(
                "BOOK_ANALYZER_TAGGING_AI_PROVIDER",
                os.getenv("BOOK_ANALYZER_DEFAULT_AI_PROVIDER", "openai"),
            ),
            tagging_model=os.getenv(
                "BOOK_ANALYZER_TAGGING_MODEL",
                os.getenv("BOOK_ANALYZER_DEFAULT_MODEL", "gpt-4o-mini"),
            ),
            tagging_temperature=float(
                os.getenv("BOOK_ANALYZER_TAGGING_TEMPERATURE", "0.1")
            ),
            tagging_max_tokens=int(
                os.getenv("BOOK_ANALYZER_TAGGING_MAX_TOKENS", "600")
            ),
            tagging_api_key=os.getenv("BOOK_ANALYZER_TAGGING_API_KEY"),
            tagging_base_url=os.getenv("BOOK_ANALYZER_TAGGING_BASE_URL"),
            embedding_provider=os.getenv("BOOK_ANALYZER_EMBEDDING_PROVIDER", "local"),
            embedding_dim=int(os.getenv("BOOK_ANALYZER_EMBEDDING_DIM", "384")),
            embedding_api_url=os.getenv("BOOK_ANALYZER_EMBEDDING_API_URL"),
            embedding_api_key=os.getenv("BOOK_ANALYZER_EMBEDDING_API_KEY"),
            embedding_api_model=os.getenv(
                "BOOK_ANALYZER_EMBEDDING_API_MODEL", "Qwen/Qwen3-VL-Embedding-8B"
            ),
            local_embedding_model=os.getenv(
                "BOOK_ANALYZER_LOCAL_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            fallback_embedding_model=os.getenv(
                "BOOK_ANALYZER_FALLBACK_EMBEDDING_MODEL",
                "sentence-transformers/all-MiniLM-L6-v2",
            ),
            chroma_collection_name=os.getenv(
                "BOOK_ANALYZER_CHROMA_COLLECTION_NAME", ""
            ),
            mcp_host=os.getenv("BOOK_ANALYZER_MCP_HOST", "0.0.0.0"),
            mcp_port=int(os.getenv("BOOK_ANALYZER_MCP_PORT", "8765")),
            batch_import_concurrency=int(
                os.getenv("BOOK_ANALYZER_BATCH_IMPORT_CONCURRENCY", "2")
            ),
        )

    def ensure_directories(self) -> None:
        """创建 data、uploads、exports、Chroma 和模型缓存等本地运行目录。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_cache_dir.mkdir(parents=True, exist_ok=True)
