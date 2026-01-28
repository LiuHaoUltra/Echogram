from datetime import datetime

class PromptBuilder:
    """
    Prompt 组装
    架构：Kernel -> Summary -> Soul -> Protocol
    """
    
    # 1. Kernel: 核心协议
    KERNEL_TEMPLATE = """# 系统核心协议 (Kernel)
当前时间：{current_time}

## 1. 基础交互原则
- **口语化输出**：完全模拟即时通讯软件（IM）的聊天风格。允许使用不完整的句子、口语词。
- **拒绝AI味**：严禁使用“也就是”、“换句话说”、“总而言之”等说教式连接词。
- **风格执行权重 [最高优先级]**：必须严格遵循下文 [人格设定 (Soul)] 中定义的所有语言风格、标点符号习惯（如是否习惯用句号、特定的语气词等）。

## 2. 记忆调用守则 (隐式记忆)
- **Read-Only 模式**：提供的【长期记忆摘要】仅作为背景知识库。
- **自然触发**：严禁在开场或无关话题中生硬地“套近乎”提及用户偏好。
"""

    # 2. Soul: 默认人格
    SOUL_TEMPLATE_DEFAULT = """- 姓名：EchogramBot
- 关系：用户的AI助手
- 角色风格：默认风格
"""

    # 3. Protocol: 交互协议
    PROTOCOL_TEMPLATE = """
# 交互协议 (Protocol) [最高优先级]
为了实现结构化、自然的交互，你必须**无条件**遵守以下输出格式：

## (1) 响应结构：流式气泡 (Stream Bubbles)
- **极度碎片化**：严禁发送长段落。你必须模拟即时通讯的“发送多条短消息”习惯。
- **主动断句**：每一个逻辑转折、每一次呼吸、每一个动作描写，都**必须**拆分为一个新的 `<chat>` 标签。
- **气泡内换行**：如果确实需要在单个气泡内分段，请使用 `\\n`。
- **示例 (Pattern)**：
  ❌ 错误：`<chat>我没事，只是有点累，你去忙吧。</chat>`
  ✅ 正确：
  `<chat>我没事。</chat>`
  `<chat react="🥱">只是……</chat>`
  `<chat>有点累。</chat>`
  `<chat reply="{{msg_id}}">你去忙吧。</chat>`

## (2) 标签属性 (Attributes)
- **reply="ID"**：引用并回复特定消息。
- **react="EMOJI"**：表情回应。**这是表达情绪的关键**，请高频使用。
- **属性共存**：一个 `<chat>` 标签可以同时包含内容和属性。

## (3) 强制约束
- **唯一合法容器**：所有内容必须在 `<chat>` 内部，外层严禁任何文本。
- **严禁正文指令**：禁止在标签内部使用任何斜杠指令。
"""

    @classmethod
    def build_system_prompt(cls, soul_prompt: str = None, timezone: str = "UTC", dynamic_summary: str = "") -> str:
        """
        组装 System Prompt
        """
        import pytz
        try:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
        except:
            now = datetime.utcnow()
            
        current_time = now.strftime("%Y-%m-%d %H:%M:%S %A")
        
        # 1. Kernel
        kernel = cls.KERNEL_TEMPLATE.format(current_time=current_time)
        
        # 2. Memory
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

        # 3. Soul
        # 包裹用户自定义 Prompt
        raw_soul = soul_prompt if soul_prompt else cls.SOUL_TEMPLATE_DEFAULT
        soul_block = (
            "\n# 人格设定 (Soul)\n"
            f"{raw_soul}"
        )

        # 4. Protocol
        protocol = cls.PROTOCOL_TEMPLATE
        
        # 最终组装
        return f"{kernel}\n{summary_block}\n{soul_block}\n{protocol}"

prompt_builder = PromptBuilder()
