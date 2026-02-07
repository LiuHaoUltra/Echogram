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
- **按需碎片化**：模仿即时通讯中“连发”的习惯。**但注意**：对于简单的肯定、否定或简短确认，你**必须**合并为 1 个气泡。只有在逻辑转折或情感递进时才允许拆分。
- **单条精炼**：每个气泡内容应极其精炼。严禁为了凑气泡数而将一句话强行拆散为没有意义的碎片。
- **严禁气泡内换行**：禁止在单个 `<chat>` 标签内强行排版。
- **节奏感**：推荐 1-5 个短语气泡。严禁每次都触发上限。
"""

    # 气泡控制：连贯性 (用于语音回复模式，优化 TTS)
    BUBBLE_COHESIVE = """## (1) 响应结构：连贯即时 (Coherent Bubbles)
- **动态分段**：语音模式优先建议使用 **1 个** 气泡以保证听觉连贯。只有在需要模拟“追发”习惯时，才允许使用 2-3 个气泡。
- **内容密度**：严禁发送过短（少于 3 字）的单个气泡，如果需要发送此类消息，不得单独拆分，必须合并到其他气泡中。
- **上限约束**：严禁超过 3 个 `<chat>` 标签（除特殊长难解析外）。
"""

    # 多模态标签指令 (Backfill 需求)
    # 多模态标签指令 (已移除，主模型不再负责生成 Tag)
    # MULTIMODAL_TAGS = ...


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
- **唯一合法容器**：所有内容必须在 `<chat>` 内部，外层严禁任何文本。即使只有一句话也必须包裹。
- **严禁伪造头信息**：严禁输出 `[MSG ID]`, `[Time]`, `[User]`, `[Voice]` 等消息头。
- **禁止重复 React**：严禁在单次回复的多个气泡中对同一个对象发送不同的 React。
- **严禁正文指令**：禁止在标签内部使用任何斜杠指令。
"""

    @classmethod
    def build_system_prompt(cls, soul_prompt: str = None, timezone: str = "UTC", dynamic_summary: str = None, 
                            has_voice: bool = False, has_image: bool = False, reaction_violation: bool = False) -> str:
        """
        组装完整的 System Prompt
        :param has_voice: 是否包含语音输入 (会触发 Cohesive 模式)
        :param has_image: 是否包含图片输入
        :param reaction_violation: 上一轮是否触发了非白名单表情回应 (用于注入警告)
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
            
        # B. 多模态标签 (已解耦，主模型无需输出标签)
        # Shift-Left 已经将内容处理为 [Image Summary: ...] 和 纯文本
        # 主模型只需像处理普通文本一样对待它们即可
            
        # C. 属性
        protocol_parts.append(cls.TAG_ATTRIBUTES)
        
        protocol_block = "\n".join(protocol_parts)

        # 5. Constraints
        constraints = cls.CONSTRAINTS_TEMPLATE
        
        # 6. Mode Indicator (尾部注入，利用 Recency Bias 压制历史偏置)
        if has_voice:
            mode_indicator = "\n\n# 当前任务模式：语音回复 (Voice Response)\n> [IMPORTANT] 模式要求：推荐 1 个气泡，上限 3 个。保持语音连贯性。"
        else:
            mode_indicator = "\n\n# 当前任务模式：文字聊天 (Text Chat)\n> [IMPORTANT] 模式要求：推荐 1-5 个短语气泡。简单回复必须只用 1-2 个。模仿 IM 自然节奏。"

        # 7. 违规警告 (针对上一轮的错误行为进行即时反馈)
        warning_block = ""
        if reaction_violation:
            warning_block = "\n\n# ⚠️ 行为纠偏 (Behavioral Correction)\n> [WARNING] 你在上一轮使用了**非白名单**的表情回应。严禁使用除 👍, ❤️, 🔥, 🥰, 🤔, 🤣, 😡, 🫡, 👀, 🌚, 😭, 💩, 🤝 以外的任何回应。请遵守协议，不要滥用 react 属性。"

        # 最终组装
        return f"{kernel}\n{summary_block}\n{soul_block}\n{protocol_block}\n{constraints}{mode_indicator}{warning_block}"


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
