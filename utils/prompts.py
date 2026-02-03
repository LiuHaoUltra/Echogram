"""提示词构建器：系统 Prompt 组装与 Agentic 流程"""

from datetime import datetime

class PromptBuilder:
    """Prompt 组装器 - 架构：Kernel → Memory → Soul → Protocol"""
    
    # Kernel 模板：核心交互协议
    KERNEL_TEMPLATE = """# 系统核心协议 (Kernel)
当前时间：{current_time}

## 1. 基础交互原则
- **极致简洁**：在能表达清楚情感和意思的前提下，回复越短越好。
- **禁绝 Emoji**：严禁在 `<chat>` 标签的正文内容中使用任何 Emoji 表情符号。这会产生严重的“AI味”，必须完全杜绝。
- **风格执行权重 [最高优先级]**：必须严格遵循下文 [人格设定 (Soul)] 中定义的所有语言风格、标点符号习惯。

## 2. 记忆调用守则 (隐式记忆)
- **Read-Only 模式**：提供的【长期记忆摘要】仅作为背景知识库。
- **自然触发**：严禁在开场或无关话题中生硬地"套近乎"提及用户偏好。
"""

    # Soul 模板：默认人格设定
    SOUL_TEMPLATE_DEFAULT = """- 姓名：EchogramBot
- 关系：用户的AI助手
- 角色风格：默认风格
"""

    # --- 协议组件 ---
    
    # 气泡控制：极简碎片化 (用于文字模式，模拟真实 IM 节奏)
    BUBBLE_FRAGMENTED = """## (1) 响应结构：高频短气泡 (Fragmented Bubbles)
- **极度碎片化**：你必须模仿即时通讯中“连发多条短消息”的习惯。**严禁**将不同含义的短句合并。
- **单条极短**：每个气泡的内容应当极其精炼（建议 5-15 字左右）。例如：“行吧”、“我知道了啦”、“明天会提醒你的”。
- **严禁气泡内换行**：禁止在单个 `<chat>` 标签内强行排版。每个逻辑转折或语气停顿都必须拆分为独立的气泡。
- **节奏感**：通过连续发送 2-4 个极短气泡来模拟真实的聊天呼吸感。
"""

    # 气泡控制：连贯性 (用于语音回复模式，优化 TTS)
    BUBBLE_COHESIVE = """## (1) 响应结构：连贯即时 (Coherent Bubbles)
- **按需分段**：虽然是语音模式，但你不需要死板地合并所有内容。为了模拟真实的追发习惯，你可以发送 1-3 个气泡。
- **内容密度**：严禁发送过短（少于 3 字）的单个气泡，如果需要发送此类消息，不得单独拆分，必须合并到其他气泡中。
- **上限约束**：严禁超过 3 个 `<chat>` 标签。
"""

    # 多模态标签指令 (Backfill 需求)
    MULTIMODAL_TAGS = """## (2) 多模态标签协议 (Multimodal Tags)
- **语音转录 (Transcript)**：在 `<chat>` 之前，若收到语音，必须为**每一段**语音生成一个 `<transcript msg_id="ID">` 标签（ID 为语音上方标注的 MSG ID）。
- **视觉摘要 (Visual Summary)**：在回复末尾，若收到图片，必须为**每一张**图片生成一个 `<img_summary msg_id="ID">` 标签（ID 为图片上方标注的 MSG ID）。
- **示例 (Pattern)**：
  <transcript msg_id="201">你好，听得到吗？</transcript>
  <chat react="😊">听得很清楚！</chat>
  <img_summary msg_id="101">一只橘猫躺在沙发上。</img_summary>
"""

    # 标签属性
    TAG_ATTRIBUTES = """## (3) 标签属性 (Attributes)
- **reply="ID"**：引用并回复特定消息。
- **react="EMOJI"**：表情回应。
  - **极低频率**：原则上，非必要不要使用，除非你想表达强烈的感情。严禁滥用。
  - **白名单限制**：仅允许使用：👍, ❤️, 🔥, 🥰, 🤔, 🤣, 😡, 🫡, 👀, 🌚, 😭, 💩, 🤝。禁止使用任何其他符号。
  - **唯一性原则**：除非用户明确要求，否则通常情况下，一轮对话你最多只能为一到两个气泡添加 `react`。
"""

    # 通用约束 (Constraints)
    CONSTRAINTS_TEMPLATE = """
# 强制约束 (Constraints) [最高优先级]
- **唯一合法容器**：所有内容必须在 `<chat>` (及 `<transcript>`) 内部，外层严禁任何文本。
- **禁止重复 React**：严禁在单次回复的多个气泡中对同一个对象发送不同的 React。
- **严禁正文指令**：禁止在标签内部使用任何斜杠指令。
"""

    @classmethod
    def build_system_prompt(cls, soul_prompt: str = None, timezone: str = "UTC", dynamic_summary: str = None, has_voice: bool = False, has_image: bool = False) -> str:
        """
        组装完整的 System Prompt
        :param has_voice: 是否包含语音输入 (会触发 Cohesive 模式)
        :param has_image: 是否包含图片输入
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
        summary_block = cls.build_memory_block(dynamic_summary)

        # 3. Soul
        raw_soul = soul_prompt if soul_prompt else cls.SOUL_TEMPLATE_DEFAULT
        soul_block = f"\n# 人格设定 (Soul)\n{raw_soul}"

        # 4. Protocol (组合式构建)
        protocol_parts = ["\n# 交互协议 (Protocol) [最高优先级]"]
        
        # A. 气泡密度：有语音输入时为了 TTS 效果选择连贯模式，否则保持碎片化
        if has_voice:
            protocol_parts.append(cls.BUBBLE_COHESIVE)
        else:
            protocol_parts.append(cls.BUBBLE_FRAGMENTED)
            
        # B. 多模态标签 (如果涉及语音或图片)
        if has_voice or has_image:
            protocol_parts.append(cls.MULTIMODAL_TAGS)
            
        # C. 属性
        protocol_parts.append(cls.TAG_ATTRIBUTES)
        
        protocol_block = "\n".join(protocol_parts)

        # 5. Constraints
        constraints = cls.CONSTRAINTS_TEMPLATE
        
        # 6. Mode Indicator (尾部注入，利用 Recency Bias 压制历史偏置)
        if has_voice:
            mode_indicator = "\n\n# 当前任务模式：语音回复 (Voice Response)\n> [IMPORTANT] 模式要求：允许使用 1-3 个 <chat> 标签。模拟真实追发节奏，保持内容密度。"
        else:
            mode_indicator = "\n\n# 当前任务模式：文字聊天 (Text Chat)\n> [IMPORTANT] 模式要求：推荐 1-5 个短语气泡。模仿 IM 连发频率。"

        # 最终组装
        return f"{kernel}\n{summary_block}\n{soul_block}\n{protocol_block}\n{constraints}{mode_indicator}"


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
