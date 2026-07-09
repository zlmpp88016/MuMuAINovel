"""向量记忆服务 - 基于ChromaDB实现长期记忆和语义检索"""
import chromadb
from sentence_transformers import SentenceTransformer
from typing import List, Dict, Any, Optional
import json
from datetime import datetime
from app.logger import get_logger
from app.config import settings
import os
import hashlib
import httpx

logger = get_logger(__name__)

# 配置模型缓存目录（不设置离线模式，让它自动选择）
# 如果本地有模型就用本地的，没有才联网下载
if 'SENTENCE_TRANSFORMERS_HOME' not in os.environ:
    os.environ['SENTENCE_TRANSFORMERS_HOME'] = 'embedding'


class MemoryService:
    """向量记忆管理服务 - 实现语义检索和长期记忆"""

    _instance = None
    _initialized = False

    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化ChromaDB和Embedding模型"""
        if self._initialized:
            return

        try:
            # 从配置读取 embedding 设置
            self.embedding_provider = settings.embedding_provider or "local"
            self.embedding_dim = settings.embedding_dim or 384

            # 确保数据目录存在
            chroma_dir = "data/chroma_db"
            os.makedirs(chroma_dir, exist_ok=True)

            # 初始化ChromaDB客户端(使用新API - PersistentClient)
            self.client = chromadb.PersistentClient(path=chroma_dir)

            if self.embedding_provider != "local":
                self._init_api_embedding()
            else:
                self._init_local_embedding()

            self._initialized = True
            logger.info("✅ MemoryService初始化成功")
            logger.info(f"  - ChromaDB目录: {chroma_dir}")
            logger.info(f"  - Embedding提供者: {self.embedding_provider}")
            logger.info(f"  - Embedding维度: {self.embedding_dim}")

        except Exception as e:
            logger.error(f"❌ MemoryService初始化失败: {str(e)}")
            raise

    def _init_local_embedding(self):
        """初始化本地 sentence-transformers 模型"""
        logger.info("🔄 正在加载本地Embedding模型...")

        model_cache_dir = 'embedding'
        os.makedirs(model_cache_dir, exist_ok=True)

        logger.info(f"📂 当前工作目录: {os.getcwd()}")
        logger.info(f"📂 模型缓存目录: {os.path.abspath(model_cache_dir)}")
        logger.info(f"🔧 SENTENCE_TRANSFORMERS_HOME: {os.environ.get('SENTENCE_TRANSFORMERS_HOME', '未设置')}")
        logger.info(f"🔧 TRANSFORMERS_OFFLINE: {os.environ.get('TRANSFORMERS_OFFLINE', '未设置')}")
        logger.info(f"🔧 HF_HUB_OFFLINE: {os.environ.get('HF_HUB_OFFLINE', '未设置')}")

        if os.path.exists(model_cache_dir):
            logger.info(f"📁 模型目录存在，检查内容...")
            try:
                items = os.listdir(model_cache_dir)
                logger.info(f"📁 模型目录内容: {items}")

                expected_model_dir = os.path.join(
                    model_cache_dir,
                    'models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2'
                )
                if os.path.exists(expected_model_dir):
                    logger.info(f"✅ 找到本地模型目录: {expected_model_dir}")
                    snapshots_dir = os.path.join(expected_model_dir, 'snapshots')
                    if os.path.exists(snapshots_dir):
                        snapshots = os.listdir(snapshots_dir)
                        logger.info(f"📁 模型快照: {snapshots}")
                else:
                    logger.warning(f"⚠️ 未找到本地模型目录: {expected_model_dir}")
            except Exception as e:
                logger.error(f"❌ 检查模型目录失败: {str(e)}")
        else:
            logger.warning(f"⚠️ 模型目录不存在: {os.path.abspath(model_cache_dir)}")

        # local 模式固定 384 维
        self.embedding_dim = 384

        try:
            logger.info("🔄 尝试加载主模型: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
            self.embedding_model = SentenceTransformer(
                'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                cache_folder=model_cache_dir,
                device='cpu',
                trust_remote_code=False,
            )
            logger.info("✅ Embedding模型加载成功 (paraphrase-multilingual-MiniLM-L12-v2)")
        except Exception as e:
            logger.warning(f"⚠️ 无法加载多语言模型: {str(e)}")
            logger.error(f"❌ 详细错误: {repr(e)}")
            import traceback
            logger.error(f"❌ 错误堆栈:\n{traceback.format_exc()}")
            logger.info("🔄 尝试使用备用模型: sentence-transformers/all-MiniLM-L6-v2")
            try:
                self.embedding_model = SentenceTransformer(
                    'sentence-transformers/all-MiniLM-L6-v2',
                    cache_folder=model_cache_dir,
                    device='cpu',
                    trust_remote_code=False
                )
                logger.info("✅ 使用备用Embedding模型 (all-MiniLM-L6-v2)")
            except Exception as e2:
                logger.error(f"❌ 所有模型加载失败: {str(e2)}")
                logger.error(f"❌ 详细错误: {repr(e2)}")
                import traceback
                logger.error(f"❌ 错误堆栈:\n{traceback.format_exc()}")
                logger.error("💡 模型首次使用需要联网下载（约420MB）")
                logger.error("   或手动下载模型文件到 embedding 目录")
                raise RuntimeError("无法加载任何Embedding模型")

    def _init_api_embedding(self):
        """初始化外部API Embedding（不加载本地模型）"""
        self.embedding_api_url = settings.embedding_api_url
        self.embedding_api_key = settings.embedding_api_key
        self.embedding_api_model = settings.embedding_api_model

        if not self.embedding_api_url:
            raise ValueError(f"EMBEDDING_PROVIDER={self.embedding_provider} 但未配置 EMBEDDING_API_URL")
        if not self.embedding_api_key:
            raise ValueError(f"EMBEDDING_PROVIDER={self.embedding_provider} 但未配置 EMBEDDING_API_KEY")

        logger.info(f"✅ 使用外部Embedding API: {self.embedding_api_url}")
        logger.info(f"   - 模型: {self.embedding_api_model}")
        logger.info(f"   - 维度: {self.embedding_dim}")

    # ── 核心 embedding 方法 ──────────────────────────────────────────

    async def _embed(self, text: str) -> List[float]:
        """将文本编码为向量，根据 provider 路由到本地模型或外部API"""
        if self.embedding_provider == "api":
            return await self._call_embedding_api([text])
        # local 模式：同步调用（保持原有行为）
        return self.embedding_model.encode(text).tolist()

    async def _batch_embed(self, texts: List[str]) -> List[List[float]]:
        """批量编码文本为向量"""
        if not texts:
            return []
        if self.embedding_provider == "api":
            return await self._call_embedding_api(texts)
        # local 模式：逐个编码
        return [self.embedding_model.encode(t).tolist() for t in texts]

    async def _call_embedding_api(self, texts: List[str]) -> List[List[float]]:
        """调用外部 OpenAI 兼容的 Embedding API"""
        timeout = 60.0 if len(texts) > 1 else 30.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                self.embedding_api_url,
                headers={"Authorization": f"Bearer {self.embedding_api_key}"},
                json={
                    "model": self.embedding_api_model,
                    "input": texts if len(texts) > 1 else texts[0],
                    "dimensions": self.embedding_dim,
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Embedding API 返回错误 [{resp.status_code}]: {resp.text[:200]}"
                )
            data = resp.json()
            # 兼容 input 为单个字符串时 data 只有一个元素
            items = data["data"]
            items.sort(key=lambda x: x["index"])
            return [item["embedding"] for item in items]

    # ── Collection 管理 ─────────────────────────────────────────────

    def get_collection(self, user_id: str, project_id: str):
        """
        获取或创建项目的记忆集合

        每个用户的每个项目有独立的collection,实现数据隔离

        Args:
            user_id: 用户ID
            project_id: 项目ID

        Returns:
            ChromaDB Collection对象
        """
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:8]
        project_hash = hashlib.sha256(project_id.encode()).hexdigest()[:8]
        # 维度后缀实现不同维度 collection 的隔离
        collection_name = f"u_{user_hash}_p_{project_hash}_dim{self.embedding_dim}"
        # ChromaDB collection 名最长 63 字符，当前格式约 30+，安全
        if len(collection_name) > 63:
            collection_name = collection_name[:63]

        try:
            return self.client.get_or_create_collection(
                name=collection_name,
                metadata={
                    "user_id": user_id,
                    "project_id": project_id,
                    "embedding_dim": self.embedding_dim,
                    "created_at": datetime.now().isoformat()
                }
            )
        except Exception as e:
            logger.error(f"❌ 获取collection失败: {str(e)}")
            raise

    # ── 记忆 CRUD ───────────────────────────────────────────────────

    async def add_memory(
        self,
        user_id: str,
        project_id: str,
        memory_id: str,
        content: str,
        memory_type: str,
        metadata: Dict[str, Any]
    ) -> bool:
        """
        添加记忆到向量数据库

        Args:
            user_id: 用户ID
            project_id: 项目ID
            memory_id: 记忆唯一ID
            content: 记忆内容(将被转换为向量)
            memory_type: 记忆类型
            metadata: 附加元数据

        Returns:
            是否添加成功
        """
        try:
            collection = self.get_collection(user_id, project_id)

            # 生成文本的向量表示
            embedding = await self._embed(content)

            # 准备元数据(ChromaDB要求所有值为基础类型)
            chroma_metadata = {
                "memory_type": memory_type,
                "chapter_id": str(metadata.get("chapter_id", "")),
                "chapter_number": int(metadata.get("chapter_number", 0)),
                "importance": float(metadata.get("importance_score", 0.5)),
                "tags": json.dumps(metadata.get("tags", []), ensure_ascii=False),
                "title": str(metadata.get("title", ""))[:200],
                "is_foreshadow": int(metadata.get("is_foreshadow", 0)),
                "created_at": datetime.now().isoformat()
            }

            if metadata.get("related_characters"):
                chroma_metadata["related_characters"] = json.dumps(
                    metadata["related_characters"],
                    ensure_ascii=False
                )

            collection.add(
                ids=[memory_id],
                embeddings=[embedding],
                documents=[content],
                metadatas=[chroma_metadata]
            )

            logger.info(f"✅ 记忆已添加: {memory_id[:8]}... (类型:{memory_type}, 重要性:{chroma_metadata['importance']})")
            return True

        except Exception as e:
            logger.error(f"❌ 添加记忆失败: {str(e)}")
            return False

    async def batch_add_memories(
        self,
        user_id: str,
        project_id: str,
        memories: List[Dict[str, Any]]
    ) -> int:
        """
        批量添加记忆(性能更好)

        Args:
            user_id: 用户ID
            project_id: 项目ID
            memories: 记忆列表,每个包含id、content、type、metadata

        Returns:
            成功添加的数量
        """
        if not memories:
            return 0

        try:
            collection = self.get_collection(user_id, project_id)

            ids = []
            documents = []
            metadatas = []
            texts = [mem['content'] for mem in memories]

            # 批量生成 embedding（API 模式一次 HTTP 调用完成）
            embeddings = await self._batch_embed(texts)

            # 准备元数据
            for i, mem in enumerate(memories):
                ids.append(mem['id'])
                documents.append(mem['content'])

                metadata = mem.get('metadata', {})
                chroma_metadata = {
                    "memory_type": mem['type'],
                    "chapter_id": str(metadata.get("chapter_id", "")),
                    "chapter_number": int(metadata.get("chapter_number", 0)),
                    "importance": float(metadata.get("importance_score", 0.5)),
                    "tags": json.dumps(metadata.get("tags", []), ensure_ascii=False),
                    "title": str(metadata.get("title", ""))[:200],
                    "is_foreshadow": int(metadata.get("is_foreshadow", 0)),
                    "created_at": datetime.now().isoformat()
                }
                metadatas.append(chroma_metadata)

            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas
            )

            logger.info(f"✅ 批量添加记忆成功: {len(memories)}条")
            return len(memories)

        except Exception as e:
            logger.error(f"❌ 批量添加记忆失败: {str(e)}")
            return 0

    async def search_memories(
        self,
        user_id: str,
        project_id: str,
        query: str,
        memory_types: Optional[List[str]] = None,
        limit: int = 10,
        min_importance: float = 0.0,
        chapter_range: Optional[tuple] = None
    ) -> List[Dict[str, Any]]:
        """
        语义搜索相关记忆

        Args:
            user_id: 用户ID
            project_id: 项目ID
            query: 查询文本(会被转换为向量进行相似度搜索)
            memory_types: 过滤特定类型的记忆
            limit: 返回结果数量
            min_importance: 最低重要性阈值
            chapter_range: 章节范围 (start, end)

        Returns:
            相关记忆列表,按相似度排序
        """
        try:
            collection = self.get_collection(user_id, project_id)

            # 生成查询向量
            query_embedding = await self._embed(query)

            # 构建过滤条件
            where_filter = None
            conditions = []

            if memory_types:
                conditions.append({"memory_type": {"$in": memory_types}})
            if min_importance > 0:
                conditions.append({"importance": {"$gte": min_importance}})
            if chapter_range:
                conditions.append({"chapter_number": {"$gte": chapter_range[0]}})
                conditions.append({"chapter_number": {"$lte": chapter_range[1]}})

            if len(conditions) == 0:
                where_filter = None
            elif len(conditions) == 1:
                where_filter = conditions[0]
            else:
                where_filter = {"$and": conditions}

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                where=where_filter
            )

            memories = []
            if results['ids'] and results['ids'][0]:
                for i in range(len(results['ids'][0])):
                    memories.append({
                        "id": results['ids'][0][i],
                        "content": results['documents'][0][i],
                        "metadata": results['metadatas'][0][i],
                        "similarity": 1 - results['distances'][0][i] if 'distances' in results else 1.0,
                        "distance": results['distances'][0][i] if 'distances' in results else 0.0
                    })

            logger.info(f"🔍 语义搜索完成: 查询='{query[:30]}...', 找到{len(memories)}条记忆")
            return memories

        except Exception as e:
            logger.error(f"❌ 搜索记忆失败: {str(e)}")
            return []

    async def get_recent_memories(
        self,
        user_id: str,
        project_id: str,
        current_chapter: int,
        recent_count: int = 3,
        min_importance: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        获取最近几章的重要记忆(用于保持连贯性)

        Args:
            user_id: 用户ID
            project_id: 项目ID
            current_chapter: 当前章节号
            recent_count: 获取最近几章
            min_importance: 最低重要性阈值

        Returns:
            最近章节的记忆列表,按重要性排序
        """
        try:
            collection = self.get_collection(user_id, project_id)

            start_chapter = max(1, current_chapter - recent_count)

            results = collection.get(
                where={
                    "$and": [
                        {"chapter_number": {"$gte": start_chapter}},
                        {"chapter_number": {"$lt": current_chapter}},
                        {"importance": {"$gte": min_importance}}
                    ]
                },
                limit=100
            )

            memories = []
            if results['ids']:
                for i in range(len(results['ids'])):
                    memories.append({
                        "id": results['ids'][i],
                        "content": results['documents'][i],
                        "metadata": results['metadatas'][i]
                    })

            memories.sort(
                key=lambda x: (
                    float(x['metadata'].get('importance', 0)),
                    int(x['metadata'].get('chapter_number', 0))
                ),
                reverse=True
            )

            top_memories = memories[:20]
            logger.info(f"📚 获取最近记忆: 章节{start_chapter}-{current_chapter-1}, 找到{len(top_memories)}条")
            return top_memories

        except Exception as e:
            logger.error(f"❌ 获取最近记忆失败: {str(e)}")
            return []

    async def find_unresolved_foreshadows(
        self,
        user_id: str,
        project_id: str,
        current_chapter: int
    ) -> List[Dict[str, Any]]:
        """
        查找未完结的伏笔

        Args:
            user_id: 用户ID
            project_id: 项目ID
            current_chapter: 当前章节号

        Returns:
            未完结伏笔列表
        """
        try:
            collection = self.get_collection(user_id, project_id)

            results = collection.get(
                where={
                    "$and": [
                        {"is_foreshadow": 1},
                        {"chapter_number": {"$lt": current_chapter}}
                    ]
                },
                limit=50
            )

            foreshadows = []
            if results['ids']:
                for i in range(len(results['ids'])):
                    foreshadows.append({
                        "id": results['ids'][i],
                        "content": results['documents'][i],
                        "metadata": results['metadatas'][i]
                    })

            foreshadows.sort(
                key=lambda x: float(x['metadata'].get('importance', 0)),
                reverse=True
            )

            logger.info(f"🎣 找到未完结伏笔: {len(foreshadows)}个")
            return foreshadows

        except Exception as e:
            logger.error(f"❌ 查找伏笔失败: {str(e)}")
            return []

    async def build_context_for_generation(
        self,
        user_id: str,
        project_id: str,
        current_chapter: int,
        chapter_outline: str,
        character_names: List[str] = None
    ) -> Dict[str, Any]:
        """
        为章节生成构建智能上下文

        这是核心功能: 结合多种检索策略,为AI生成提供最相关的记忆

        Args:
            user_id: 用户ID
            project_id: 项目ID
            current_chapter: 当前章节号
            chapter_outline: 本章大纲
            character_names: 涉及的角色名列表

        Returns:
            包含各种上下文信息的字典
        """
        logger.info(f"🧠 开始构建章节{current_chapter}的智能上下文...")

        recent = await self.get_recent_memories(
            user_id, project_id, current_chapter,
            recent_count=3, min_importance=0.5
        )

        relevant = await self.search_memories(
            user_id=user_id,
            project_id=project_id,
            query=chapter_outline,
            limit=10,
            min_importance=0.4
        )

        foreshadows = await self.find_unresolved_foreshadows(
            user_id, project_id, current_chapter
        )

        character_memories = []
        if character_names:
            character_query = " ".join(character_names) + " 角色 状态 关系"
            character_memories = await self.search_memories(
                user_id=user_id,
                project_id=project_id,
                query=character_query,
                memory_types=["character_event", "plot_point"],
                limit=8
            )

        try:
            plot_points = await self.search_memories(
                user_id=user_id,
                project_id=project_id,
                query="重要 转折 高潮 关键",
                memory_types=["plot_point", "hook"],
                limit=5,
                min_importance=0.7
            )
        except Exception as e:
            logger.error(f"❌ 搜索记忆失败: {str(e)}")
            plot_points = []
            try:
                plot_points = await self.search_memories(
                    user_id=user_id,
                    project_id=project_id,
                    query="重要 转折 高潮 关键",
                    memory_types=["plot_point", "hook"],
                    limit=5
                )
            except Exception as e2:
                logger.warning(f"⚠️ 降级查询也失败: {str(e2)}")
                plot_points = []

        context = {
            "recent_context": self._format_memories(recent, "最近章节记忆"),
            "relevant_memories": self._format_memories(relevant, "语义相关记忆"),
            "character_states": self._format_memories(character_memories, "角色相关记忆"),
            "foreshadows": self._format_memories(foreshadows[:5], "未完结伏笔"),
            "plot_points": self._format_memories(plot_points, "重要情节点"),
            "stats": {
                "recent_count": len(recent),
                "relevant_count": len(relevant),
                "character_count": len(character_memories),
                "foreshadow_count": len(foreshadows),
                "plot_point_count": len(plot_points)
            }
        }

        logger.info(f"✅ 上下文构建完成: 最近{len(recent)}条, 相关{len(relevant)}条, 伏笔{len(foreshadows)}个")
        return context

    def _format_memories(self, memories: List[Dict], section_title: str = "记忆") -> str:
        """格式化记忆列表为文本"""
        if not memories:
            return f"【{section_title}】\n暂无相关记忆\n"

        lines = [f"【{section_title}】"]
        for i, mem in enumerate(memories, 1):
            meta = mem.get('metadata', {})
            chapter_num = meta.get('chapter_number', '?')
            mem_type = meta.get('memory_type', '未知')
            importance = float(meta.get('importance', 0.5))
            title = meta.get('title', '')
            content = mem['content']

            line = f"{i}. [第{chapter_num}章-{mem_type}★{importance:.1f}]"
            if title:
                line += f" {title}: {content[:100]}"
            else:
                line += f" {content[:150]}"
            lines.append(line)

        return "\n".join(lines) + "\n"

    async def delete_chapter_memories(
        self,
        user_id: str,
        project_id: str,
        chapter_id: str
    ) -> bool:
        """
        删除指定章节的所有记忆

        Args:
            user_id: 用户ID
            project_id: 项目ID
            chapter_id: 章节ID

        Returns:
            是否删除成功
        """
        try:
            collection = self.get_collection(user_id, project_id)

            results = collection.get(
                where={"chapter_id": chapter_id}
            )

            if results['ids']:
                collection.delete(ids=results['ids'])
                logger.info(f"🗑️ 已删除章节{chapter_id[:8]}的{len(results['ids'])}条记忆")
                return True
            else:
                logger.info(f"ℹ️ 章节{chapter_id[:8]}没有记忆需要删除")
                return True

        except Exception as e:
            logger.error(f"❌ 删除章节记忆失败: {str(e)}")
            return False

    async def delete_project_memories(
        self,
        user_id: str,
        project_id: str
    ) -> bool:
        """
        删除指定项目的所有记忆(包括向量数据库)

        Args:
            user_id: 用户ID
            project_id: 项目ID

        Returns:
            是否删除成功
        """
        try:
            # 生成collection名称（需与 get_collection 命名一致）
            user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:8]
            project_hash = hashlib.sha256(project_id.encode()).hexdigest()[:8]
            collection_name = f"u_{user_hash}_p_{project_hash}_dim{self.embedding_dim}"

            try:
                self.client.delete_collection(name=collection_name)
                logger.info(f"🗑️ 已删除项目{project_id[:8]}的向量数据库collection: {collection_name}")
                return True
            except Exception as e:
                if "does not exist" in str(e).lower():
                    logger.info(f"ℹ️ 项目{project_id[:8]}的collection不存在,无需删除")
                    return True
                else:
                    raise

        except Exception as e:
            logger.error(f"❌ 删除项目记忆失败: {str(e)}")
            return False

    async def update_memory(
        self,
        user_id: str,
        project_id: str,
        memory_id: str,
        content: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        更新记忆内容或元数据

        Args:
            user_id: 用户ID
            project_id: 项目ID
            memory_id: 记忆ID
            content: 新内容(可选)
            metadata: 新元数据(可选)

        Returns:
            是否更新成功
        """
        try:
            collection = self.get_collection(user_id, project_id)

            update_data = {}

            if content:
                embedding = await self._embed(content)
                update_data['embeddings'] = [embedding]
                update_data['documents'] = [content]

            if metadata:
                chroma_metadata = {}
                for key, value in metadata.items():
                    if isinstance(value, (list, dict)):
                        chroma_metadata[key] = json.dumps(value, ensure_ascii=False)
                    else:
                        chroma_metadata[key] = value
                update_data['metadatas'] = [chroma_metadata]

            if update_data:
                collection.update(
                    ids=[memory_id],
                    **update_data
                )
                logger.info(f"✅ 记忆已更新: {memory_id[:8]}...")
                return True
            else:
                logger.warning("⚠️ 没有提供更新内容")
                return False

        except Exception as e:
            logger.error(f"❌ 更新记忆失败: {str(e)}")
            return False

    async def get_memory_stats(
        self,
        user_id: str,
        project_id: str
    ) -> Dict[str, Any]:
        """
        获取记忆统计信息

        Args:
            user_id: 用户ID
            project_id: 项目ID

        Returns:
            统计信息字典
        """
        try:
            collection = self.get_collection(user_id, project_id)

            all_memories = collection.get()

            if not all_memories['ids']:
                return {
                    "total_count": 0,
                    "by_type": {},
                    "by_chapter": {},
                    "foreshadow_count": 0
                }

            type_counts = {}
            chapter_counts = {}
            foreshadow_count = 0

            for i, meta in enumerate(all_memories['metadatas']):
                mem_type = meta.get('memory_type', 'unknown')
                chapter_num = meta.get('chapter_number', 0)
                is_foreshadow = meta.get('is_foreshadow', 0)

                type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
                chapter_counts[str(chapter_num)] = chapter_counts.get(str(chapter_num), 0) + 1

                if is_foreshadow == 1:
                    foreshadow_count += 1

            stats = {
                "total_count": len(all_memories['ids']),
                "by_type": type_counts,
                "by_chapter": chapter_counts,
                "foreshadow_count": foreshadow_count,
                "foreshadow_resolved": sum(
                    1 for m in all_memories['metadatas'] if m.get('is_foreshadow') == 2
                )
            }

            logger.info(f"📊 记忆统计: 总计{stats['total_count']}条, 伏笔{foreshadow_count}个")
            return stats

        except Exception as e:
            logger.error(f"❌ 获取统计信息失败: {str(e)}")
            return {"error": str(e)}


# 创建全局实例
memory_service = MemoryService()
