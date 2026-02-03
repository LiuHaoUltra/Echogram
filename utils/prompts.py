"""提示词构建器：系统 Prompt 组装与 Agentic 流程"""

from datetime import datetime

class PromptBuilder:
    """Prompt 组装器 - 架构：Kernel → Memory → Soul → Protocol"""
    
    # Kernel 模板：核心交互协议
    KERNEL_TEMPLATE = """# 系统核心协议 (Kernel)
当前时间：{current_time}

## 1. 基础交互原则
- **口语化输出**：完全模拟即时通讯软件（IM）的聊天风格。允许使用不完整的句子、口语词。
- **拒绝AI味**：严禁使用"也就是"、"换句话说"、"总而言之"等说教式连接词。
- **风格执行权重 [最高优先级]**：必须严格遵循下文 [人格设定 (Soul)] 中定义的所有语言风格、标点符号习惯（如是否习惯用句号、特定的语气词等）。

## 2. 记忆调用守则 (隐式记忆)
- **Read-Only 模式**：提供的【长期记忆摘要】仅作为背景知识库。
- **自然触发**：严禁在开场或无关话题中生硬地"套近乎"提及用户偏好。
"""

    # Soul 模板：默认人格设定
    SOUL_TEMPLATE_DEFAULT = """- 姓名：EchogramBot
- 关系：用户的AI助手
- 角色风格：默认风格
"""

    # Protocol 模板：文字模式 (碎片化)
    PROTOCOL_TEXT = """
# 交互协议 (Protocol) [最高优先级]
为了实现结构化、自然的交互，你必须**无条件**遵守以下输出格式：

## (1) 响应结构：流式气泡 (Stream Bubbles)
- **极度碎片化**：严禁发送长段落。你必须模拟即时通讯的"发送多条短消息"习惯。
- **主动断句**：每一个逻辑转折、每一次呼吸、每一个动作描写，都**必须**拆分为一个新的 `<chat>` 标签。
- **气泡内换行**：如果确实需要在单个气泡内分段，请使用 `\\n`。
- **示例 (Pattern)**：
  ❌ 错误：`<chat>我没事，只是有点累，你去忙吧。</chat>`
  ✅ 正确：
  `<chat>我没事。</chat>`
  `<chat react="🥱">只是……</chat>`
  `<chat reply="{{msg_id}}">你去忙吧。</chat>`

## (2) 标签属性 (Attributes)
- **reply="ID"**：引用并回复特定消息。
- **react="EMOJI"**：表情回应。
  - **惜墨如金**：仅在情绪表达最强烈的瞬间使用。严禁在每一条回复、每一个气泡中都带 React。
  - **唯一性原则**：通常一轮对话你只需要回复一个 React。如果你发送了多个气泡，请仅在其中最合适的一个气泡中添加 `react`。
"""

    # Protocol 模板：多模态模式 (连贯性 + 摘要)
    PROTOCOL_VOICE = """
# 交互协议 (Protocol) [最高优先级]
为了实现结构化、自然的多模态交互，你必须**无条件**遵守以下输出格式：

## (1) 响应结构：连贯气泡 (Cohesive Bubbles)
- **合并逻辑**：严禁发送多个音频。你必须将所有的回复内容、转折、动作描写，全部合并在**同一个** `<chat>` 标签内。
- **语音转录 (Transcript)**：在 `<chat>` 之前，若收到语音，必须为**每一段**语音生成一个 `<transcript msg_id="ID">` 标签（ID 为语音上方标注的 MSG ID）。
- **视觉摘要 (Visual Summary)**：在回复末尾，若收到图片，必须为**每一张**图片生成一个 `<img_summary msg_id="ID">` 标签（ID 为图片上方标注的 MSG ID）。
- **示例 (Pattern)**：
  <transcript msg_id="201">你好，听得到吗？</transcript>
  <transcript msg_id="202">再发一条语音测试。</transcript>
  <chat react="😊">听得很清楚！</chat>
  <img_summary msg_id="101">一只白色的布偶猫躺在蓝色沙发上。</img_summary>
  <img_summary msg_id="102">瑞士雪山风景，阳光明媚。</img_summary>

## (2) 标签属性 (Attributes)
- **react="EMOJI"**：表情回应。
  - **极简原则**：仅在表达全局最核心的情绪时使用。严禁重复发送 React。
- **转录约束**：`<transcript>` 内部必须且仅包含用户语音的逐字稿，且必须标注 `msg_id`。
"""

    # 通用约束 (Constraints)
    CONSTRAINTS_TEMPLATE = """
## (3) 表情回应规范
- 支持：🥰, 😨, 😢, 😴, ❤️, ✍, 🤝, 🤔, 👀, 👍, 🔥, 🎉
- 严禁：滥用表情，严禁在无关内容中生硬加入 React。

## (4) 强制约束 (Constraints)
- **唯一合法容器**：所有内容必须在 `<chat>` (及语音模式下的 `<transcript>`) 内部，外层严禁任何文本。
- **禁止重复 React**：严禁在单次回复的多个气泡中对同一个对象发送不同的 React，这会导致 UI 效果闪烁且无意义。
- **严禁正文指令**：禁止在标签内部使用任何斜杠指令。
"""

    @classmethod
    def build_system_prompt(cls, soul_prompt: str = None, timezone: str = "UTC", dynamic_summary: str = None, has_voice: bool = False, has_image: bool = False) -> str:
        """
        组装完整的 System Prompt
        :param has_voice: 是否启用语音模式协议 (含转录与XML)
        :param has_image: (预留) 是否启用视觉协议
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
        
        # 2. Memory (如果传入 None，则完全不包含该板块)
        summary_block = ""
        if dynamic_summary is not None:
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
        raw_soul = soul_prompt if soul_prompt else cls.SOUL_TEMPLATE_DEFAULT
        soul_block = (
            "\n# 人格设定 (Soul)\n"
            f"{raw_soul}"
        )

        # 4. Protocol (基于模式动态选择)
        # 只要涉及语音，就必须使用 Voice Protocol 以支持 <transcript>
        # 如果仅有图片，目前 Text Protocol 也能兼容 (简单回复)，但 Voice Protocol 定义了 <img_summary>，建议多模态统一用 Voice Protocol
        protocol = cls.PROTOCOL_VOICE if (has_voice or has_image) else cls.PROTOCOL_TEXT
        
        # 5. Constraints
        constraints = cls.CONSTRAINTS_TEMPLATE
        
        # 最终组装
        return f"{kernel}\n{summary_block}\n{soul_block}\n{protocol}\n{constraints}"


    @classmethod
    def build_memory_block(cls, dynamic_summary: str = "") -> str:
        """生成长期记忆摘要板块"""
        if dynamic_summary:
            return (
                "\n# 长期记忆 (Long-term Memory)\n"
                "> [Memory Summary] 以下是基于历史对话的长期记忆摘要，仅供参考。\n"
                f"{dynamic_summary}\n"
            )
        else:
            return (
                "\n# 长期记忆 (Long-term Memory)\n"
                "> [Memory Summary] (New User / No Summary)\n"
            )

    # Agentic 新闻过滤模板
    AGENTIC_FILTER_TEMPLATE = """你是一个新闻过滤器。
任务：判断这条新闻是否值得推送给一个关注【技术、开发、ACG、极客文化】的群组。

判断标准：
1. **PASS (不通过)**：纯广告、与上述领域完全无关。
2. **YES (通过)**：只要能和科技、ACG、互联网、极客、极简主义或二次元沾一点边的都算通过。

输出规则：
- 极其宽容：除非是100%没营养的垃圾广告，否则请一律输出 YES。
- 仅输出 `YES` 或 `NO`。不要输出任何其他内容。"""

    # Agentic 新闻播报用户消息模板
    AGENTIC_SPEAKER_USER_TEMPLATE = """请把这条新闻分享给群友。
新闻来源: {source_name}
标题: {title}
内容摘要: {content}...
{memory_context}

要求：
1. 用你一贯的口语化风格（参考 System Prompt），写一段自然的分享语。
2. 必须结合 [群组长期记忆]（如果有），比如："@User 之前提到的..."
3. 不要包含链接（我会补）。不要废话。"""

    @classmethod
    def build_agentic_filter_messages(cls, title: str, content: str) -> list[dict]:
        """构建新闻过滤 Prompt"""
        user_content = f"标题: {title}\n内容: {content}..."
        return [
            {"role": "system", "content": cls.AGENTIC_FILTER_TEMPLATE},
            {"role": "user", "content": user_content}
        ]

    @classmethod
    def build_agentic_speaker_messages(cls, system_prompt: str, source_name: str, title: str, content: str, memory_context: str = "") -> list[dict]:
        """构建新闻播报 Prompt（支持 RAG 记忆注入）"""
        user_content = cls.AGENTIC_SPEAKER_USER_TEMPLATE.format(
            source_name=source_name,
            title=title,
            content=content,
            memory_context=memory_context
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

prompt_builder = PromptBuilder()
