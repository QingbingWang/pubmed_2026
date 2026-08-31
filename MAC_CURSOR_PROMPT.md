# Mac 本地化 — 复制到 Cursor 的提示词

将下方「提示词正文」整段复制到 **Mac 上 Cursor** 的新对话中发送即可。  
请先把压缩包解压到目标目录（例如 `~/Projects/pumed_local`），再用 Cursor 打开该文件夹。

---

## 提示词正文（复制从下一行开始）

```text
你是本机 Cursor Agent。请把当前工作区的「PubMed 本地语义检索与全文学习系统」（Flask + GLM/DeepSeek + PubMed）在 macOS 上完整本地化并跑通。这是从 Windows 移植过来的，代码本身跨平台，但启动脚本与虚拟环境路径不同。

## 目标
1. 在本机创建可用的 Python 虚拟环境并安装依赖
2. 确保 `.env` 配置可用（至少 GLM 或 DeepSeek 一个 Key）
3. 用 Mac 启动脚本拉起服务，浏览器能打开 http://127.0.0.1:5000
4. 快速冒烟验证三个标签页相关 API 不报错
5. 如发现 Windows 残留路径、CRLF、权限问题，直接修好

## 项目事实（不要改业务逻辑，除非为跑通所必需）
- 入口：`app.py`（Flask，`FLASK_HOST`/`FLASK_PORT`，默认 127.0.0.1:5000）
- 配置：`config.py` + `.env`（可参考 `.env.example`）
- 依赖：`requirements.txt`（flask、requests、python-dotenv、openai、pypdf）
- 功能页：检索内容 / 摘要问答 / 全文学习；可选 Notion 导出
- 数据目录：`data/abstracts/`、`data/pdfs/`、`data/pdfs_workspace/`、`data/abstract_qa/`、`data/study_qa/`
- Mac 启动脚本：`start.sh` / `快速启动.sh`（虚拟环境用 `.venv/bin/python`，不是 Windows 的 `Scripts\python.exe`）
- Windows 的 `.bat` 可保留，Mac 不用
- 不要提交/打印完整 API Key；检查配置时只确认「已配置/未配置」

## 请按顺序执行

### A. 环境检查
- 确认 macOS、当前工作区路径、Python 版本（需要 3.10+）
- 若无 Python：给出 `brew install python@3.12` 并在装好后继续（不要假装已安装）
- 给 `start.sh`、`快速启动.sh` 加执行权限：`chmod +x start.sh 快速启动.sh`

### B. 虚拟环境与依赖
在项目根目录执行：
  rm -rf .venv          # 若存在从 Windows 同步来的损坏 venv，必须删掉重建
  python3 -m venv .venv
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
- 确认 `.venv/bin/python` 可导入 flask、openai、pypdf、dotenv

### C. 配置
- 若无 `.env`：从 `.env.example` 复制
- 检查 `GLM_API_KEY` / `DEEPSEEK_API_KEY` 至少一个有效（非占位符 `your_api_key_here`）
- 若 Key 缺失：明确告诉我要填哪些变量，暂停启动
- 可选：`NCBI_API_KEY`、`NOTION_API_KEY`、`NOTION_DATABASE_ID`
- 若 Mac 在国外访问智谱慢，可提示改用 `GLM_BASE_URL=https://api.z.ai/api/paas/v4/`（仅建议，先用现有配置试）

### D. 启动
优先：./start.sh
或：.venv/bin/python app.py
- 用浏览器或 curl 访问 `http://127.0.0.1:5000` 确认首页 200
- 若端口占用：改 `.env` 的 `FLASK_PORT` 或帮我找出占用进程

### E. 冒烟验证（尽量自动做）
1. GET `/` 首页可打开
2. 若有配置 Key：调用提供方列表/健康相关接口（按现有 `app.py` 路由），确认 provider 显示 configured
3. 确认 `data/` 下目录存在且可写
4. 全文学习依赖 Chrome 的文件夹选择（`showDirectoryPicker` / `webkitdirectory`）；在说明里注明推荐 Chrome/Edge，Safari 可能受限
5. 不要对外部 PubMed/LLM 发起大规模真实检索，除非我明确要求；最多做一次轻量连通性检查

### F. 交付给我
用简洁中文回复：
1. 最终项目路径
2. 以后如何启动（一条命令）
3. `.env` 里哪些关键项已就绪/仍缺
4. 你改动过的文件列表（如有）
5. 已知 Mac 注意点（venv 勿同步、浏览器建议、权限）

## 约束
- 不要引入无关重构或新依赖
- 不要删除 `data/` 里已有摘要/问答数据
- 不要把密钥写进 README 或新文档
- 完成后保持服务可启动；若你在后台启动了服务，告诉我 PID/如何停止
```

---

## 传输与解压备忘（人工步骤）

1. 在 Windows 已生成：`dist/pumed_local_mac_*.zip`（不含 `.venv`）
2. 用网盘/U 盘拷到 Mac
3. 解压：
   ```bash
   cd ~/Projects
   unzip ~/Downloads/pumed_local_mac_YYYYMMDD_HHMMSS.zip
   cd pumed_local
   ```
4. 用 Cursor打开该目录，粘贴上方提示词
5. 若 zip 内已含个人 `.env`，解压后注意文件权限，勿上传到公开仓库：
   ```bash
   chmod 600 .env
   ```

## 若使用百度网盘同步整库（不走 zip）

仍建议在 Mac 上：
```bash
rm -rf .venv
chmod +x start.sh 快速启动.sh
./start.sh
```
Windows 的 `.venv` 不能直接用；同步冲突时以源码 + `data/` + `.env` 为准。
