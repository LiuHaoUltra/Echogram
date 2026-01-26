# Echogram

![GitHub](https://img.shields.io/github/license/LiuHaoUltra/Echogram)
![GitHub last commit](https://img.shields.io/github/last-commit/LiuHaoUltra/Echogram)

Echogram 是一个基于 Python 的轻量级 Telegram Bot AI 伴侣框架。

## ✨ 特性

- **ChatOps**: 通过 `/dashboard` 在 Telegram 内配置所有参数（API Key, Base URL, Model Name, System Prompt）。
- **隐私优先**: 敏感操作强制私聊，白名单机制严格控制访问权限。
- **动态人格**: 随时修改 AI 的 System Prompt，即刻生效。
- **滚动记忆**: 自动维护短期对话上下文。
- **轻量部署**: 基于 SQLite，单一容器即可运行。

## 🚀 快速开始

### 方式一：Docker Compose (推荐)

Echogram 专为容器化环境设计，建议使用 Docker Compose 启动。

1. **拉取代码**：
   ```bash
   git clone https://github.com/LiuHaoUltra/Echogram.git
   cd Echogram
   ```

2. **配置环境变量**：
   ```bash
   cp .env.example .env
   # 编辑 .env 文件填入 TG_BOT_TOKEN 和 ADMIN_USER_ID
   ```

3. **构建并启动**：
   ```bash
   docker compose up -d --build
   ```

### 方式二：Dockge 部署

请按照以下步骤在 Dockge 中部署：

1. 打开终端，进入 Dockge 管理的 Stacks 目录（例如 `/opt/stacks`）。
2. 克隆本项目：
   ```bash
   git clone https://github.com/LiuHaoUltra/Echogram.git echogram
   ```
3. 回到 Dockge Web 面板，点击右上角 `+ Scan Stacks Folder` (扫描堆栈文件夹)。
4. 在列表中找到 `echogram`，点击进入。
5. 点击 `Edit` 按钮，在 Environment Variables 区域配置 `.env` 变量。
6. 点击 `Deploy` 按钮，Dockge 将自动构建并启动服务。

## 🛠️ 分步配置

1. **初始化**: 对 Bot 发送 `/dashboard`（确保你的 Admin ID 设置正确）。
2. **配置 API**: 在面板中选择 `📡 API Settings`，填入你的 LLM 服务商信息（如 OpenRouter）。
   - Base URL: `https://openrouter.ai/api/v1`
   - Model Name: `google/gemini-3.0-pro-preview`
3. **设置人格**: 在 `🧠 Persona` 中输入新的 System Prompt。
4. **群组使用**: 
   - 将 Bot 拉入群组。
   - 确保已在 BotFather 处关闭 Privacy Mode（详见 [指南](docs/TELEGRAM_GUIDE.md)）。
   - 在 Dashboard 中选择 `🛡️ Access Control` -> `Add Whitelist ID` 添加群组 ID。
   - 在群里直接说话即可触发。

## 📚 文档
- [Telegram Bot 设置指南](docs/TELEGRAM_GUIDE.md)
   