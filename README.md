# 个人知识库 · 纸上档案

一个在本机运行的 Markdown 知识库。把笔记导入 VHS 书架后，可以按原文提问、切换讲解或分步教学，再通过练习和到期复习跟踪学习进度。

## 功能

- **文档管理** · 导入 UTF-8 Markdown，确认同名替换，重建索引，查看当前和历史引用对应的原文。
- **检索问答** · 本地 BGE 向量与 BM25L 双路召回，使用 RRF 融合，可选 BGE 重排。问答结果用 SSE 流式返回，引用可在右侧原文栏打开，栏宽可拖动。
- **学习与复习** · 在同一会话中选择学习模式和教学方式，完成单选练习、错题补讲与变式练习。浏览器保存聊天列表，SQLite 保存服务端学习状态和复习计划。

## 在 Windows 启动

需要 Python 3.12 或较新的兼容版本。以下命令在项目根目录的 PowerShell 执行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item -LiteralPath backend/.env.example -Destination backend/.env
```

已有 `backend/.env` 时直接编辑已有文件。填写 `DEEPSEEK_API_KEY` 后即可使用模型问答、教学和出题。文档管理与原文查看可在模型密钥为空时使用。首次建立向量索引需要下载嵌入模型，或提前准备本机模型缓存。

双击 **`一键启动融合知识库.cmd`**。服务就绪后会打开 [融合知识库](http://127.0.0.1:8765/fusion)。使用期间保持启动窗口开启，按 Ctrl+C 停止服务。端口冲突时可以换一个本机端口。

```powershell
.\.venv\Scripts\python.exe start_fusion.py --port 8766
```

已有 Python 环境的 Linux 或 macOS 用户也可安装相同依赖后执行 `python start_fusion.py`。接口文档位于 

![夸克截图\_2026-11-19\_13-37-23.png](<file:///C:/Users/Lenovo/Downloads/%E5%A4%B8%E5%85%8B%E6%88%AA%E5%9B%BE_2026-11-19_13-37-23.png>)

![夸克截图\_2026-11-19\_13-36-04.png](<file:///C:/Users/Lenovo/Downloads/%E5%A4%B8%E5%85%8B%E6%88%AA%E5%9B%BE_2026-11-19_13-36-04.png>)

![夸克截图\_2026-11-19\_13-36-32.png](<file:///C:/Users/Lenovo/Downloads/%E5%A4%B8%E5%85%8B%E6%88%AA%E5%9B%BE_2026-11-19_13-36-32.png>)

![夸克截图\_2026-11-19\_13-36-46.png](<file:///C:/Users/Lenovo/Downloads/%E5%A4%B8%E5%85%8B%E6%88%AA%E5%9B%BE_2026-11-19_13-36-46.png>)

> 

## 导入第一份笔记

1. 新建一个 UTF-8 文件 `会议笔记.md`，写入标题 `# 周会` 和一行正文，例如「每周三上午十点召开项目例会」。
2. 在网页点击「导入 Markdown 笔记」，选择文件，再点击「导入并建立索引」。
3. 打开「问答与学习」，问「项目例会何时召开？」生成答案后点击引用查看原文。
4. 在文档页可以替换、删除和重建索引。把文件直接放入 `backend/data/knowledge/` 后也可手动重建索引。

## 数据与配置

| 设置 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` | 配置生成服务。模型请求可能包含问题和检索到的笔记片段。 |
| `RAW_DIR` | 可选的外部 Markdown 资料目录。 |
| `INDEX_DIR` | 可选的索引目录。Windows 默认使用 `%LOCALAPPDATA%\FusionKnowledgeBase` 下的项目专属目录。若自定义，请使用 ASCII 路径。 |
| `ENABLE_RERANK` | 使用本地重排模型，首次使用可能需要下载模型。 |
| `WEB_SEARCH_MCP_CONFIG` | 可选的 MCP 搜索配置文件。保持为空时使用本地检索及配置允许的模型回退。 |

服务默认只监听 `127.0.0.1`，面向本机单用户。索引目录的上一级保存应用访问令牌和 SQLite 学习记录。本机页面自动获取应用访问令牌。DeepSeek API Key 只在后端配置。真实 `.env`、个人笔记、索引和数据库由本机保存，已加入 Git 忽略规则。

![夸克截图\_2026-11-19\_13-36-32.png](<>)

## 项目结构

```text
frontend/                  VHS 书架、问答工作台、同源 API 客户端和视觉素材
  index.html               预生成的页面，启动时由 FastAPI 提供
  build-demo.cjs           从本目录源文件和素材重新生成页面
backend/
  app/main.py              网页、文档、会话、复习与进度接口
  app/rag/                 分块、嵌入、索引、混合检索与重排
  app/graph/               LangGraph 问答与教学流程
  app/learning/            会话、测验、复习与学习进度
  app/knowledge/           文档更新、快照与原文读取
  .env.example             配置模板
start_fusion.py            本机服务入口
一键启动融合知识库.cmd      Windows 双击入口
```

编辑前端后，从根目录执行 `node frontend/build-demo.cjs` 重新生成页面。日常启动使用提交的 HTML，无需 Node.js。页面使用原型中的 VHS 图集，内嵌字体的许可见 [字体许可](frontend/assets/licenses/)。


