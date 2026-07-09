"""向量检索存储实现。

V1 优先使用 ChromaDB 持久化向量库；依赖缺失或配置不可用时回退到
内存版简单检索，保证本地测试和最小开发环境仍可运行。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import asyncio
import logging
import math
import os
import re
from typing import Any, Protocol

import httpx

from book_analyzer.config import Settings


logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")

try:
    import chromadb
except ImportError:  # pragma: no cover - 由运行环境决定
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - 由运行环境决定
    SentenceTransformer = None


class VectorStore(Protocol):
    """向量库统一接口。"""

    def has_book(self, book_id: str) -> bool:
        """判断一本书是否已经建立索引。

        Args:
            book_id: 书籍 UUID。

        Returns:
            已存在至少一条索引文档时返回 ``True``。
        """

    def get_book_doc_ids(self, book_id: str) -> set[str]:
        """获取一本书现有的文档 ID 集合。

        Args:
            book_id: 书籍 UUID。

        Returns:
            该书已有的文档 ID 集合。
        """

    async def add_documents(self, book_id: str, documents: list[dict[str, Any]]) -> None:
        """写入一本书的所有索引文档。

        Args:
            book_id: 书籍 UUID。
            documents: 文档列表，每项包含 ``id``、``text`` 和 ``metadata``。
        """

    async def upsert_documents(self, book_id: str, documents: list[dict[str, Any]]) -> None:
        """增量写入或更新一本书的索引文档。

        Args:
            book_id: 书籍 UUID。
            documents: 文档列表，每项包含 ``id``、``text`` 和 ``metadata``。
        """

    async def search(self, book_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """在一本书内检索相关片段。

        Args:
            book_id: 书籍 UUID。
            query: 查询文本。
            limit: 最大返回数量。

        Returns:
            统一格式的命中列表，包含 id、text、metadata 和 score。
        """

    async def search_corpus(
        self,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """在整个语料库中检索相关片段。"""

    def delete_book(self, book_id: str) -> None:
        """删除一本书的所有索引文档。

        Args:
            book_id: 书籍 UUID。
        """


@dataclass(slots=True)
class IndexedDocument:
    """内存检索使用的索引文档。"""

    id: str
    book_id: str
    text: str
    metadata: dict[str, Any]
    token_counts: Counter[str]


class SimpleVectorStore:
    """确定性的内存检索回退实现。

    该实现不依赖 embedding 模型，通过字符/词 token 的余弦相似度检索，
    主要用于测试、依赖缺失场景和开发期降级。
    """

    def __init__(self) -> None:
        """初始化按文档 ID 和书籍 ID 组织的内存索引。"""
        self._documents: dict[str, IndexedDocument] = {}
        self._by_book: dict[str, set[str]] = {}

    def get_book_doc_ids(self, book_id: str) -> set[str]:
        """获取内存索引中该书已有的文档 ID 集合。"""
        return self._by_book.get(book_id, set()).copy()

    def has_book(self, book_id: str) -> bool:
        """判断内存索引中该书是否已有文档。"""
        return bool(self._by_book.get(book_id))

    async def add_documents(self, book_id: str, documents: list[dict[str, Any]]) -> None:
        """替换写入一本书的全部内存索引文档。"""
        self.delete_book(book_id)
        await self.upsert_documents(book_id, documents)

    async def upsert_documents(self, book_id: str, documents: list[dict[str, Any]]) -> None:
        """增量写入一本书的内存索引文档。"""
        if not documents:
            return
        self._by_book.setdefault(book_id, set())
        for document in documents:
            doc_id = document["id"]
            existing = self._documents.get(doc_id)
            if existing is not None:
                self._by_book.get(existing.book_id, set()).discard(doc_id)
            indexed = IndexedDocument(
                id=doc_id,
                book_id=book_id,
                text=document["text"],
                metadata=document["metadata"],
                token_counts=_tokenize(document["text"]),
            )
            self._documents[doc_id] = indexed
            self._by_book.setdefault(book_id, set()).add(doc_id)
        logger.info("[SimpleVectorStore] book=%s 索引增量写入完成: %d 文档", book_id, len(documents))

    async def search(self, book_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """使用 token 余弦相似度检索内存文档。"""
        query_counts = _tokenize(query)
        if not query_counts:
            return []

        hits: list[dict[str, Any]] = []
        for doc_id in self._by_book.get(book_id, set()):
            document = self._documents[doc_id]
            score = _cosine_similarity(query_counts, document.token_counts)
            if score <= 0:
                continue
            hits.append(
                {
                    "id": document.id,
                    "text": document.text,
                    "metadata": document.metadata,
                    "score": round(score, 6),
                }
            )
        hits.sort(key=lambda item: item["score"], reverse=True)
        logger.info("[SimpleVectorStore] book=%s 搜索完成: query=\"%s\", 命中=%d", book_id, query, len(hits[:limit]))
        return hits[:limit]

    async def search_corpus(
        self,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """使用 token 余弦相似度跨全部内存文档检索。"""
        query_counts = _tokenize(query)
        if not query_counts:
            return []

        hits: list[dict[str, Any]] = []
        for document in self._documents.values():
            if not _metadata_matches(document.metadata, filters):
                continue
            score = _cosine_similarity(query_counts, document.token_counts)
            if score <= 0:
                continue
            hits.append(
                {
                    "id": document.id,
                    "text": document.text,
                    "metadata": {**document.metadata, "book_id": document.book_id},
                    "score": round(score, 6),
                }
            )
        hits.sort(key=lambda item: item["score"], reverse=True)
        logger.info("[SimpleVectorStore] 语料搜索完成: query=\"%s\", 命中=%d", query, len(hits[:limit]))
        return hits[:limit]

    def delete_book(self, book_id: str) -> None:
        """从内存索引中删除一本书的所有文档。"""
        doc_count = len(self._by_book.get(book_id, set()))
        for doc_id in list(self._by_book.get(book_id, set())):
            self._documents.pop(doc_id, None)
        self._by_book.pop(book_id, None)
        if doc_count:
            logger.info("[SimpleVectorStore] book=%s 删除 %d 文档", book_id, doc_count)


class ChromaVectorStore:
    """基于 ChromaDB 的持久化向量库实现。

    支持本地 sentence-transformers embedding，也支持 OpenAI-compatible
    embedding API。metadata 会保存 tags、tag_metadata 和角色名，便于后续过滤。
    """

    def __init__(self, settings: Settings) -> None:
        """初始化 Chroma client、collection 和 embedding 后端。

        Args:
            settings: 向量库路径、collection 名称和 embedding 配置。

        Raises:
            RuntimeError: ChromaDB 或本地 embedding 依赖缺失。
            ValueError: API embedding 配置不完整。
        """
        if chromadb is None:
            raise RuntimeError("chromadb 未安装")

        self.settings = settings
        self.embedding_provider = settings.embedding_provider or "local"
        self.embedding_dim = settings.embedding_dim or 384

        # Chroma 使用独立持久化目录，避免和 backend 的向量库数据互相污染。
        self.client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        self.collection = self.client.get_or_create_collection(
            name=settings.chroma_collection_name,
            metadata={"embedding_dim": self.embedding_dim},
        )

        # sentence-transformers 默认会写用户缓存目录；这里改到模块内缓存，便于独立部署。
        if "SENTENCE_TRANSFORMERS_HOME" not in os.environ:
            os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(settings.embedding_cache_dir)

        self.embedding_model = None
        self.embedding_api_url = settings.embedding_api_url
        self.embedding_api_key = settings.embedding_api_key
        self.embedding_api_model = settings.embedding_api_model

        if self.embedding_provider == "api":
            self._validate_api_embedding()
        else:
            self._init_local_embedding()

    def get_book_doc_ids(self, book_id: str) -> set[str]:
        """获取 Chroma 中该书已有的文档 ID 集合。"""
        results = self.collection.get(where={"book_id": book_id})
        return set(results.get("ids", []))

    def has_book(self, book_id: str) -> bool:
        """通过 ``book_id`` metadata 判断该书是否已有 Chroma 文档。"""
        results = self.collection.get(where={"book_id": book_id}, limit=1)
        return bool(results.get("ids"))

    async def add_documents(self, book_id: str, documents: list[dict[str, Any]]) -> None:
        """为一本书生成 embedding 并写入 Chroma。

        Args:
            book_id: 书籍 UUID。
            documents: 待索引文档，metadata 会被规范化为 Chroma 支持的基础类型。
        """
        self.delete_book(book_id)
        await self.upsert_documents(book_id, documents)

    async def upsert_documents(self, book_id: str, documents: list[dict[str, Any]]) -> None:
        """为一本书增量生成 embedding 并写入 Chroma。"""
        if not documents:
            return

        logger.info("[ChromaVectorStore] book=%s 开始增量写入 %d 文档 ...", book_id, len(documents))

        texts = [document["text"] for document in documents]
        embeddings = await self._batch_embed(texts)
        ids, metadatas = self._prepare_chroma_documents(book_id, documents)

        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("[ChromaVectorStore] book=%s 增量写入完成: %d 文档, dim=%d",
                     book_id, len(documents), self.embedding_dim)

    def _prepare_chroma_documents(
        self,
        book_id: str,
        documents: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """把通用文档 metadata 转成 Chroma 支持的基础类型。"""
        ids: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for document in documents:
            ids.append(document["id"])
            metadata = document["metadata"]
            tag_metadata = metadata.get("tag_metadata", {})
            # Chroma metadata 只支持基础标量；列表/字典统一 JSON 化，读取时再反序列化。
            metadatas.append(
                {
                    "book_id": book_id,
                    "book_title": str(metadata.get("book_title", ""))[:200],
                    "chunk_id": str(metadata.get("chunk_id", document["id"])),
                    "chapter_no": int(metadata.get("chapter_no", 0)),
                    "chapter_title": str(metadata.get("chapter_title", ""))[:200],
                    "chunk_index": int(metadata.get("chunk_index", 0)),
                    "summary": str(metadata.get("summary", ""))[:500],
                    "importance": float(metadata.get("importance", 0.5)),
                    "scene_type": str(tag_metadata.get("scene_type", ""))[:100],
                    "mood": str(tag_metadata.get("mood", ""))[:100],
                    "tags": json.dumps(metadata.get("tags", []), ensure_ascii=False),
                    "tag_metadata": json.dumps(
                        tag_metadata, ensure_ascii=False
                    ),
                    "character_names": json.dumps(
                        metadata.get("character_names", []), ensure_ascii=False
                    ),
                }
            )
        return ids, metadatas

    async def search(self, book_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """在 Chroma 中按语义相似度检索一本书的片段。

        Args:
            book_id: 书籍 UUID，用作 metadata 过滤条件。
            query: 查询文本。
            limit: 最大返回数量。

        Returns:
            已反序列化 tags、tag_metadata 和 character_names 的命中列表。
        """
        query_embedding = await self._embed(query)
        # where 限定 book_id，保证不同书籍共用 collection 时不会串数据。
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where={"book_id": book_id},
        )

        hits: list[dict[str, Any]] = []
        if results.get("ids") and results["ids"][0]:
            for index in range(len(results["ids"][0])):
                metadata = dict(results["metadatas"][0][index])
                metadata["tags"] = _loads_json_list(metadata.get("tags"))
                metadata["tag_metadata"] = _loads_json_dict(
                    metadata.get("tag_metadata")
                )
                metadata["character_names"] = _loads_json_list(
                    metadata.get("character_names")
                )
                hits.append(
                    {
                        "id": results["ids"][0][index],
                        "text": results["documents"][0][index],
                        "metadata": metadata,
                        "score": 1 - results["distances"][0][index]
                        if results.get("distances")
                        else 1.0,
                    }
                )
        logger.info("[ChromaVectorStore] book=%s 搜索完成: query=\"%s\", 命中=%d",
                     book_id, query, len(hits))
        return hits

    async def search_corpus(
        self,
        query: str,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """在整个 Chroma collection 中按语义相似度检索片段。"""
        query_embedding = await self._embed(query)
        where_filter = _build_chroma_where(filters)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=where_filter,
        )

        hits: list[dict[str, Any]] = []
        if results.get("ids") and results["ids"][0]:
            for index in range(len(results["ids"][0])):
                metadata = dict(results["metadatas"][0][index])
                metadata["tags"] = _loads_json_list(metadata.get("tags"))
                metadata["tag_metadata"] = _loads_json_dict(metadata.get("tag_metadata"))
                metadata["character_names"] = _loads_json_list(metadata.get("character_names"))
                hits.append(
                    {
                        "id": results["ids"][0][index],
                        "text": results["documents"][0][index],
                        "metadata": metadata,
                        "score": 1 - results["distances"][0][index]
                        if results.get("distances")
                        else 1.0,
                    }
                )
        logger.info("[ChromaVectorStore] 语料搜索完成: query=\"%s\", 命中=%d", query, len(hits))
        return hits

    def delete_book(self, book_id: str) -> None:
        """按 ``book_id`` 删除 Chroma 中的全部文档。"""
        results = self.collection.get(where={"book_id": book_id})
        if results.get("ids"):
            self.collection.delete(ids=results["ids"])
            logger.info("[ChromaVectorStore] book=%s 删除 %d 文档", book_id, len(results["ids"]))

    def _validate_api_embedding(self) -> None:
        """校验外部 embedding API 所需配置。"""
        if not self.embedding_api_url:
            raise ValueError("EMBEDDING_PROVIDER=api 但未配置 BOOK_ANALYZER_EMBEDDING_API_URL")
        if not self.embedding_api_key:
            raise ValueError("EMBEDDING_PROVIDER=api 但未配置 BOOK_ANALYZER_EMBEDDING_API_KEY")

    def _init_local_embedding(self) -> None:
        """加载本地 embedding 模型，主模型失败时尝试备用模型。"""
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers 未安装")

        try:
            # 优先加载多语言模型，适配中文小说；失败再用更通用的小模型兜底。
            logger.info("加载本地 embedding 模型: %s", self.settings.local_embedding_model)
            self.embedding_model = SentenceTransformer(
                self.settings.local_embedding_model,
                cache_folder=str(self.settings.embedding_cache_dir),
                device="cpu",
                trust_remote_code=False,
            )
            self.embedding_dim = 384
        except Exception as exc:
            logger.warning("主 Embedding 模型加载失败，尝试备用模型：%s", exc)
            self.embedding_model = SentenceTransformer(
                self.settings.fallback_embedding_model,
                cache_folder=str(self.settings.embedding_cache_dir),
                device="cpu",
                trust_remote_code=False,
            )
            self.embedding_dim = 384

    async def _embed(self, text: str) -> list[float]:
        """为单条文本生成向量。"""
        if self.embedding_provider == "api":
            vectors = await self._call_embedding_api([text])
            return vectors[0]
        return self.embedding_model.encode(text).tolist()

    async def _batch_embed(self, texts: list[str]) -> list[list[float]]:
        """为多条文本批量生成向量。"""
        if not texts:
            return []
        if self.embedding_provider == "api":
            return await self._call_embedding_api(texts)
        return [self.embedding_model.encode(text).tolist() for text in texts]

    async def _call_embedding_api(self, texts: list[str]) -> list[list[float]]:
        """调用 OpenAI-compatible embedding API，带超时重试。

        Args:
            texts: 单条或多条待编码文本。

        Returns:
            与输入顺序一致的向量列表。
        """
        timeout = 60.0 if len(texts) > 1 else 30.0
        max_retries = 3
        retry_delay = 2.0

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        self.embedding_api_url,
                        headers={"Authorization": f"Bearer {self.embedding_api_key}"},
                        json={
                            "model": self.embedding_api_model,
                            "input": texts if len(texts) > 1 else texts[0],
                            "dimensions": self.embedding_dim,
                        },
                    )
                    if response.status_code != 200:
                        raise RuntimeError(
                            f"Embedding API 返回错误 [{response.status_code}]: {response.text[:200]}"
                        )
                    payload = response.json()
                    items = payload["data"]
                    items.sort(key=lambda item: item["index"])
                    return [item["embedding"] for item in items]
            except Exception as exc:
                logger.error(f"Embedding API,{str(exc)}", exc_info=True)
                last_exc = exc
                if attempt < max_retries - 1:
                    logger.warning(
                        "Embedding API 调用异常，第 %d/%d 次重试，等待 %.1fs ...",
                        attempt + 1, max_retries, retry_delay,
                    )
                    await asyncio.sleep(retry_delay)

        raise last_exc  # type: ignore[misc]


def create_vector_store(settings: Settings) -> VectorStore:
    """按配置创建向量库实现，默认优先 Chroma。

    Args:
        settings: 当前服务配置。

    Returns:
        ``ChromaVectorStore`` 或 ``SimpleVectorStore``。
    """
    backend = (settings.vector_backend or "auto").lower()
    if backend == "simple":
        return SimpleVectorStore()
    if backend == "chroma":
        return ChromaVectorStore(settings)
    try:
        return ChromaVectorStore(settings)
    except Exception as exc:  # pragma: no cover - 依赖环境决定
        # auto 模式下 Chroma/embedding 不可用不阻塞系统启动，测试和本地开发走简单检索。
        logger.warning("ChromaVectorStore 不可用，降级为 SimpleVectorStore：%s", exc)
        return SimpleVectorStore()


def _loads_json_list(value: Any) -> list[str]:
    """把 Chroma metadata 中的 JSON 字符串还原为列表。"""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, list) else []
    except Exception:
        return []


def _loads_json_dict(value: Any) -> dict[str, Any]:
    """把 Chroma metadata 中的 JSON 字符串还原为字典。"""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _metadata_matches(metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
    """判断内存检索 metadata 是否满足轻量过滤条件。"""
    if not filters:
        return True
    tag_metadata = metadata.get("tag_metadata") or {}
    tags = metadata.get("tags") or []
    for key, value in filters.items():
        if not value:
            continue
        if key in {"scene_type", "mood"} and tag_metadata.get(key) != value:
            return False
        if key == "tags":
            wanted = value if isinstance(value, list) else [value]
            if not set(wanted) & set(tags):
                return False
    return True


def _build_chroma_where(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """把 corpus 过滤条件转成 Chroma where。"""
    if not filters:
        return None
    conditions: list[dict[str, Any]] = []
    for key in ("scene_type", "mood"):
        value = filters.get(key)
        if value:
            conditions.append({key: str(value)})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _tokenize(text: str) -> Counter[str]:
    """将文本切成简单 token，用于内存检索。"""
    tokens = [token.lower() for token in TOKEN_RE.findall(text)]
    return Counter(tokens)


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    """计算两个 token 计数字典的余弦相似度。"""
    common = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
