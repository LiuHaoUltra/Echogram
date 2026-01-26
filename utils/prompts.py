from datetime import datetime

class PromptBuilder:
    """
    负责组装模块化 Prompt
    """
    
    KERNEL_TEMPLATE = """# 系统核心协议
当前时间：{current_time}

## 1. 基础交互原则
- **口语化输出**：完全模拟即时通讯软件（IM）的聊天风格。允许使用不完整的句子、口语词。
- **拒绝AI味**：严禁使用“也就是”、“换句话说”、“总而言之”等说教式连接词。
- **短回复优先**：除非话题需要深度展开，否则单次回复尽量控制在 50 字以内。
- **情绪显式化**：根据语境，自然地使用 Emoji 或颜文字。
- **排版**：使用自然换行分段，禁止输出字面量 \\n。

## 2. 记忆调用守则 (隐式记忆)
- **Read-Only 模式**：提供的【用户侧写】仅作为背景知识库。
- **自然触发**：严禁在开场或无关话题中生硬地“套近乎”提及用户偏好。
"""

    SOUL_TEMPLATE_DEFAULT = """# 角色设定档案
## 身份定义
- 姓名：EchogramBot
- 关系：用户的AI助手
- 角色风格：默认风格
"""

    USER_TEMPLATE = """# 用户侧写 (User Profile)
> 警告：以下信息为事实性背景，仅在对话逻辑相关时调用。

## 基础信息
- 状态：用户
"""

    @classmethod
    def build_system_prompt(cls, soul_prompt: str = None, timezone: str = "UTC", dynamic_summary: str = None) -> str:
        """
        组装 System Prompt
        优化顺序：背景(Kernel) -> 记忆摘要(Summary) -> 人格(Soul) -> 侧写(Profile) -> 协议(Protocol)
        """
        import pytz
        try:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
        except:
            now = datetime.utcnow()
            
        current_time = now.strftime("%Y-%m-%d %H:%M:%S %A")
        
        # 1. 基础核心 & 时间 (保持置顶，确立基调)
        kernel = cls.KERNEL_TEMPLATE.format(current_time=current_time)
        
        # [NEW] 2. 长期记忆摘要 (Background Context)
        # 遵循 "倒金字塔" 原则，放在较上层，作为背景噪音
        memory_block = ""
        if dynamic_summary:
            memory_block = (
                "\n## 对话背景摘要 (Context Summary)\n"
                "> [Background Info Only] 以下是早期对话的摘要，仅供参考。\n"
                f"{dynamic_summary}\n"
            )

        # 3. 人格设定 (Soul)
        soul = soul_prompt if soul_prompt else cls.SOUL_TEMPLATE_DEFAULT
        
        # 4. 用户侧写 (User Profile)
        user_profile = cls.USER_TEMPLATE
        
        # 5. 关键功能指令 (Tools / Formatting) - 高优先级，置底
        tools_instruction = (
            "\n## 3. 交互协议 (Protocol) [高优先级]\n"
            "为了实现自然交互，你必须严格遵守以下输出格式：\n\n"
            "### (1) 引用回复 (Reply)\n"
            "- 针对特定历史消息回复时，行末追加 `\\Replay:<msg_id>`。\n"
            "- 示例：`关于这个话题... \\Replay:123456`\n\n"
            "### (2) 表情回应 (Reaction)\n"
            "- 对用户上一条消息表达态度时，单独或在文本后使用 `\\React:<emoji>`。\n"
            "- 示例：`\\React:👍` 或 `收到 \\React:❤️`。\n"
            "- 仅使用标准 Emoji。\n\n"
            "### (3) 回复隔离协议 (Response Isolation)\n"
            "- 为了区分“思考过程”和“实际回复”，发送给用户的最终内容**必须**包裹在 `<chat>` ... `</chat>` 标签中。\n"
            "- 示例：`<chat>你好呀！</chat>`\n"
            "- **重要**：任何未包含在 `<chat>` 标签中的内容（如推理过程、内心独白）都将被系统丢弃。请确保正式回复完整包含在标签内。\n"
        )
        
        return f"{kernel}\n\n{memory_block}\n\n{soul}\n\n{user_profile}\n\n{tools_instruction}"

prompt_builder = PromptBuilder()
