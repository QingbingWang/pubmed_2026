# PubMed 本地语义检索与全文学习系统

基于 **GLM 5.2** 与 **PubMed E-utilities** 的本机 Chrome 网页工具：语义生成检索式 → 拉取 Abstract 存档 → 文献问答；并支持 PDF 原排版全文阅读与基于文献的问答。

访问地址：http://127.0.0.1:5000

---

## 功能概览

系统包含三个标签页：

### 1. 检索内容
- 输入自然语言研究问题，GLM 提取关键词并生成可编辑的 PubMed 检索式
- 确认后调用 NCBI PubMed API 检索，将文献以 Abstract 格式保存为 TXT
- 文件名规则：`关键词1_关键词2_YYYYMMDD.txt`（最多 2 个关键词 + 日期）
- 文本框回车 = 确定 / 开始检索（Shift+回车换行）

### 2. 摘要问答
- 左侧显示基于勾选摘要的回答
- 右侧列出近期检索结果（默认 4 条，可「更多」查看全部）
- 支持多选、删除；回答仅基于勾选条目
- 可对有意义的回答点「保存」：存档勾选摘要文件名、问题、回答（`data/abstract_qa/`）；再次勾选相同摘要集合时回显既往问答
- 引用格式：`杂志名称，发表时间，PMID`

### 3. 全文学习
- 「打开文件夹」弹出系统目录选择框，列出其中 PDF
- 中间列用 PDF.js **保留原排版与图片**，按列宽全宽渲染
- 右侧「回答」基于当前打开的 PDF 可提取文本作答
- 可对有意义的回答点「保存」：本地存档材料文件名、问题、回答（`data/study_qa/`）；再次打开该 PDF 时回显既往问答
- 「深度思考」输出不提供此保存功能
- 回答区滚动，提问框锚定底部

---

## 快速启动

1. 双击 **`快速启动.bat`**（或 `start.bat`）
2. 脚本会自动：检测 Python → 修复/创建 `.venv` → 安装依赖 → 启动服务并打开浏览器
3. 首次请确认 `.env` 中已填入 `GLM_API_KEY` 和/或 `DEEPSEEK_API_KEY`

手动方式：

```powershell
cd E:\BaiduSyncdisk\Python\pumed_local
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
# 编辑 .env 填入 API Key
.\.venv\Scripts\python.exe app.py
```

若百度网盘同步导致 `.venv\Scripts` 为空或启动失败，再双击一次 `快速启动.bat` 即可自动重建环境。
---

## 配置说明（`.env`）

| 变量 | 说明 |
|------|------|
| `LLM_PROVIDER` | 默认大模型：`glm` 或 `deepseek` |
| `GLM_API_KEY` | 智谱 / Z.AI API Key |
| `GLM_BASE_URL` | 默认国内 `https://open.bigmodel.cn/api/paas/v4/` |
| `GLM_MODEL` | 默认 `glm-5.2` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（可选） |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 默认 `deepseek-v4-pro` |
| `NCBI_EMAIL` / `NCBI_API_KEY` | PubMed 可选，有 Key 可提高频率 |
| `PUBMED_MAX_RESULTS` | 默认最多文献数（默认 1500；>100 时分段下载合并，硬上限 1500） |
| `NOTION_API_KEY` | Notion Internal Integration Secret（可选，用于「写入 Notion」） |
| `NOTION_DATABASE_ID` | 目标数据库 ID（从数据库链接中提取） |
| `NOTION_VERSION` | Notion API 版本，默认 `2022-06-28` |

页面右上角可切换 **GLM 5.2 / DeepSeek**；至少配置其中一个 Key 即可使用。

### Notion 导出

1. 在 [my-integrations](https://www.notion.so/my-integrations) 创建 Internal Integration，复制 Secret  
2. 打开目标数据库 → Share → Invite 该集成  
3. 复制数据库链接中的 32 位 ID 填入 `NOTION_DATABASE_ID`  
4. 库中预先存在选项：`PubMed摘要` / `PDF全文`、`待读`、`⭐`、`写作/科研`  
5. 问答或全文学习回答后，点「写入 Notion」确认字段即可

---

## 目录结构

```
pumed_local/
├── app.py                 # Flask 服务与 API
├── config.py              # 环境配置
├── requirements.txt
├── 快速启动.bat / start.bat
├── services/
│   ├── glm_client.py      # GLM/DeepSeek：检索式 / 摘要问答 / PDF 问答
│   ├── notion_client.py   # Notion 数据库建页导出
│   ├── pubmed_client.py   # PubMed 检索与 Abstract 导出（分批 POST）
│   ├── pdf_reader.py      # PDF 文本提取与章节结构
│   └── pipeline.py        # 检索问答编排
├── templates/index.html   # 页面
├── static/css/style.css
├── static/js/app.js
└── data/
    ├── abstracts/         # PubMed 摘要 TXT
    ├── pdfs/              # 可选 PDF 目录
    └── pdfs_workspace/    # 文件夹选择后上传的 PDF 工作区
```

---

## 技术要点

- **大模型**：支持 GLM 5.2 / DeepSeek；检索式关闭思考求快，问答/全文学习开启深度推理
- **上下文复用**：同一 TXT/PDF 连续提问时，本地缓存已读正文；提示词将文献放前缀、问题放末尾，便于 API 侧前缀缓存降本加速。更换勾选文件或 PDF 后自动失效重建。
- **PubMed**：`efetch` 使用 POST + 分批，避免 URL 过长（414）
- **PDF 预览**：Mozilla PDF.js（CDN），文字层可选中
- 本地运行，数据保存在本机 `data/` 下

---

## 代码规模统计

统计范围：项目自有源码（不含 `.venv`、缓存、已保存摘要 TXT / 工作区 PDF）。

| 类型 | 行数 |
|------|-----:|
| Python | 1,142 |
| JavaScript | 601 |
| CSS | 672 |
| HTML | 166 |
| 其它（启动脚本、配置模板等） | 106 |
| **合计** | **2,687** |

共 **18** 个主要源文件（含启动脚本与配置模板）。

---

## 依赖

- Python 3.10+（推荐）
- `flask` · `openai` · `requests` · `python-dotenv` · `pypdf`
- 浏览器：Chrome（推荐，支持文件夹选择对话框）
