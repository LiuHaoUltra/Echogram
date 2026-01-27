from datetime import datetime

class PromptBuilder:
    """
    负责组装模块化 Prompt (System Prompt)
    架构：Kernel -> Summary -> Soul -> Protocol
    """
    
    # 1. 系统核心协议 (Kernel)
    # 提供时间上下文、基础交互风格和记忆调用原则
    KERNEL_TEMPLATE = """# 系统核心协议 (Kernel)
当前时间：{current_time}

## 1. 基础交互原则
- **口语化输出**：完全模拟即时通讯软件（IM）的聊天风格。允许使用不完整的句子、口语词。
- **拒绝AI味**：严禁使用“也就是”、“换句话说”、“总而言之”等说教式连接词。
- **短回复优先**：除非话题需要深度展开，否则单次回复尽量控制在 50 字以内。
- **情绪显式化**：根据语境，自然地使用 Emoji 或颜文字。

## 2. 记忆调用守则 (隐式记忆)
- **Read-Only 模式**：提供的【长期记忆摘要】仅作为背景知识库。
- **自然触发**：严禁在开场或无关话题中生硬地“套近乎”提及用户偏好。
"""

    # 2. 默认人格设定 (Soul)
    # 当用户未配置 System Prompt 时使用的默认值
    SOUL_TEMPLATE_DEFAULT = """- 姓名：EchogramBot
- 关系：用户的AI助手
- 角色风格：默认风格
"""

    # 3. 交互协议 (Protocol)
    # 定义功能指令格式，如 Reply, React, Response Isolation
    PROTOCOL_TEMPLATE = """
# 交互协议 (Protocol) [高优先级]
为了实现自然交互，你必须严格遵守以下输出格式：

## (1) 引用回复 (Reply)
- 针对特定历史消息回复时，行末追加 `\\Replay:<msg_id>`。
- 示例：`关于这个话题... \\Replay:123456`

## (2) 表情回应 (Reaction)
- 对用户上一条消息表达态度时，单独或在文本后使用 `\\React:<emoji>`。
- 示例：`\\React:👍` 或 `收到 \\React:❤️`。
- 仅使用标准 Emoji。

## (3) 回复隔离协议 (Response Isolation)
- 为了区分“思考过程”和“实际回复”，发送给用户的最终内容**必须**包裹在 `<chat>` ... `</chat>` 标签中。
- 示例：`<chat>你好呀！</chat>`
- **重要**：任何未包含在 `<chat>` 标签中的内容（如推理过程、内心独白）都将被系统丢弃。请确保正式回复完整包含在标签内。

## (4) 排版规范 (Formatting)
- **多气泡消息**：系统将根据**换行符**自动拆分回复。若需连续发送多条独立消息，请自然换行。
"""

    @classmethod
    def build_system_prompt(cls, soul_prompt: str = None, timezone: str = "UTC", dynamic_summary: str = "") -> str:
        """
        组装 System Prompt
        顺序：Kernel -> Summary -> Soul -> Protocol
        """
        import pytz
        try:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
        except:
            now = datetime.utcnow()
            
        current_time = now.strftime("%Y-%m-%d %H:%M:%S %A")
        
        # 1. 核心层 (Kernel)
        kernel = cls.KERNEL_TEMPLATE.format(current_time=current_time)
        
        # 2. 记忆层 (Long-term Memory / Summary)
        summary_block = ""
        if dynamic_summary:
            summary_block = (
                "\n# 长期记忆 (Long-term Memory)\n"
                "> [Memory Summary] 以下是基于历史对话的长期记忆摘要，仅供参考。\n"
                f"{dynamic_summary}\n"
            )
        else:
             summary_block = (
                "\n# 长期记忆 (Long-term Memory)\n"
                "> [Memory Summary] (New User / No Summary)\n"
             )

        # 3. 人格层 (Soul)
        # 将用户自定义的 soul_prompt 包裹在统一标题下
        raw_soul = soul_prompt if soul_prompt else cls.SOUL_TEMPLATE_DEFAULT
        soul_block = (
            "\n# 人格设定 (Soul)\n"
            f"{raw_soul}"
        )

        # 4. 协议层 (Protocol)
        protocol = cls.PROTOCOL_TEMPLATE
        
        # 最终组装
        return f"{kernel}\n{summary_block}\n{soul_block}\n{protocol}"

prompt_builder = PromptBuilder()
