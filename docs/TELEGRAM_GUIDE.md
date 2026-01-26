# Telegram Bot 设置指南

本指南将协助你通过 BotFather 创建并配置 Telegram Bot，以便配合 Echogram 使用。

## 1. 创建新 Bot

1.  打开 Telegram，搜索 **[@BotFather](https://t.me/BotFather)** 并点击 "Start"。
2.  发送命令 `/newbot`。
3.  按照提示输入 Bot 的**显示名称** (Name)，例如 `My AI Assistant`。
4.  按照提示输入 Bot 的**用户名** (Username)。即使是测试用的 Bot，**必须以 `bot` 结尾**，例如 `echogram_demo_bot`。
5.  创建成功后，BotFather 会给你一个 **HTTP API Token**。
    > ⚠️ **重要**: 请妥善保存此 Token，填入 `.env` 文件的 `TG_BOT_TOKEN` 字段。

## 2. 获取管理员 ID (Admin User ID)

你需要将自己的 Telegram User ID 填入 `.env` 的 `ADMIN_USER_ID`，以便获得最高管理权限。

1.  在 Telegram 搜索 **[@userinfobot](https://t.me/userinfobot)**。
2.  点击 "Start" 或发送任何消息。
3.  它会回复你的 ID (例如 `123456789`)。

## 3. 关闭隐私模式 (Privacy Mode)

为了让 Bot 在群组中能接收到消息（而不仅仅是特定指令或被回复时），建议关闭隐私模式。

1.  给 BotFather 发送 `/mybots`。
2.  选择你刚才创建的 Bot。
3.  点击 **Bot Settings**。
4.  点击 **Group Privacy**。
5.  点击 **Turn off**。
    > 状态应显示为: *Group Privacy is disabled.*

## 4. 设置指令列表 (可选)

为了方便使用，建议将指令注册到菜单中。

1.  给 BotFather 发送 `/setcommands`。
2.  选择你的 Bot。
3.  粘贴以下内容：

```text
start - 开始对话
dashboard - 打开控制面板 (仅管理员)
clear - 清除当前上下文记忆
help - 获取帮助信息
```

## 5. 允许群组访问 (可选)

如果你希望将 Bot 拉入群组：

1.  在 BotFather 中选择 `/mybots` -> 选择 Bot -> **Bot Settings**。
2.  点击 **Allow Groups?**。
3.  确保状态为 **Turn groups on**。

---

配置完成后，请确保 `.env` 文件已正确填写，然后重启 Echogram 容器。
