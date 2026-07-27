# Book Analyzer

独立于 `backend/` 和 `frontend/` 的 TXT 书籍分析系统。

当前版本提供：

- TXT 上传
- 章节解析与分块
- 结构化摘要生成
- 书内搜索
- JSON 导出

同时已经按原项目思路接入了两套可选能力：

- `LLM`：沿用 `provider/base_url/model` 配置方式
- `Vector DB`：沿用 `local/api` 双模式 embedding + `ChromaDB PersistentClient`

如果环境中缺少依赖或没有配置密钥，服务会自动降级到本地规则分析与简单搜索实现，方便测试和本地开发。

## 项目结构

```text
book-analyzer/
├── README.md                 # 项目说明与启动方式
├── pyproject.toml            # Python 包信息、运行依赖和测试配置
├── requirements.txt          # pip 安装用运行依赖清单
├── .env.example              # 环境变量示例，包含 LLM、tagging、embedding、存储配置
├── .gitignore                # 本模块忽略规则
├── book_analyzer/            # 独立服务源码包
│   ├── __init__.py           # 包导出入口
│   ├── main.py               # FastAPI 应用工厂，初始化依赖和路由
│   ├── api.py                # HTTP API 层，负责请求/响应转换
│   ├── service.py            # 核心业务流程：上传、解析、分析、索引、导出、删除
│   ├── config.py             # 独立配置读取与运行目录初始化
│   ├── db.py                 # SQLAlchemy engine、session 和轻量 schema 兼容
│   ├── models.py             # 本系统自己的 books、book_chunks ORM 模型
│   ├── schemas.py            # API 请求/响应 Pydantic 模型
│   ├── parser.py             # TXT 解码、章节识别和重叠切块
│   ├── analysis.py           # 规则/LLM 分析器，生成 chunk 和整书分析结果
│   ├── tagging.py            # 段落打标签小模型接入，失败回退规则标签
│   ├── llm_service.py        # OpenAI-compatible / Anthropic 文本生成封装
│   ├── vector_store.py       # Chroma 向量库与内存检索回退实现
│   └── static/               # 内置零构建页面，可上传、预览、查看详情和搜索
├── tests/                    # 单元与 API 流程测试
│   ├── conftest.py           # 测试配置和 TestClient fixture
│   ├── test_parser.py        # TXT 解码、章节解析、切块测试
│   ├── test_tagging.py       # 段落打标签小模型与规则回退测试
│   └── test_vector_store.py  # 简单向量检索回退测试
└── data/                     # 运行时数据目录，本地生成，不作为源码维护
    ├── uploads/              # 上传 TXT 备份
    ├── exports/              # 分析结果 JSON 导出
    └── chroma/               # ChromaDB 持久化数据
```

## 配置

```bash
copy book-analyzer\.env.example book-analyzer\.env
```

服务启动时会自动读取 `book-analyzer/.env`，但同名系统环境变量优先，方便线上部署覆盖本地配置。

Chroma 默认使用 `BOOK_ANALYZER_VECTOR_BACKEND=auto`：依赖和 embedding 模型可用时写入 `book-analyzer/data/chroma`，不可用时自动降级为简单内存检索。需要强制使用 Chroma 时设置 `BOOK_ANALYZER_VECTOR_BACKEND=chroma`；需要指定持久化目录时设置 `BOOK_ANALYZER_CHROMA_DIR=data/chroma` 或绝对路径。

## 启动

```bash
pip install -r analyzer-book/requirements.txt
```

```bash
uvicorn book_analyzer.main:app --reload --app-dir analyzer-book
```

启动后访问：

- 页面：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`

## MCP 服务

MCP 服务与管理用 HTTP API 独立启动，共享 analyzer-book 自己的数据库和
Chroma 索引。在仓库根目录执行：

```bash
cd analyzer-book
python -m book_analyzer.mcp_server
```

默认 endpoint 为 `http://127.0.0.1:8765/mcp`。如需跨机器访问，请通过
`BOOK_ANALYZER_MCP_HOST` 和 `BOOK_ANALYZER_MCP_PORT` 配置监听地址，并在
外层增加 TLS、鉴权和网络访问控制，不要直接将无认证服务暴露到公网。

当前提供 6 个只读 tools：

- `corpus_search_reference_passages`：检索场景与文风参考片段；
- `corpus_get_highlight_passages`：检索去 AI 味校准片段；
- `corpus_get_style_profile`：读取书级风格画像；
- `corpus_search_plot_patterns`：返回去情节化的节拍与冲突模式；
- `corpus_search_character_archetypes`：返回匿名角色原型；
- `corpus_find_foreshadow_patterns`：返回伏笔埋设、回收或配对技法。

可用真实 streamable HTTP 检查工具发现和 schema：

```bash
python scripts/check_mcp_contract.py
```

固定查询评测会输出 P95、跨书多样性、标签命中率以及已人工标注查询的
Recall@K/Precision@K。`relevant_book_ids` 在
`benchmarks/corpus_queries.json` 中维护；少于 50 本时规模门禁应保持
`blocked`：

```bash
python scripts/benchmark_corpus.py --corpus-book-count 50 --summary-only
```

如果本机未安装 Chroma，而 `.env` 显式配置了 `vector_backend=chroma`，可先用
`BOOK_ANALYZER_VECTOR_BACKEND=simple` 启动进行契约检查。

## 测试

```bash
pytest book-analyzer/tests
```
