"""消息智能分割：保护代码块完整性"""

import re

def split_message(text: str) -> list[str]:
    """
    智能拆分长消息，保护代码块不被破坏
    :param text: 原始消息
    :return: 拆分后的消息列表
    """
    if not text:
        return []

    # 1. 提取并保护代码块
    code_blocks = []
    
    def replacer(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    # 匹配 ```code``` 格式
    pattern = re.compile(r"```.*?```", re.DOTALL)
    text_safe = pattern.sub(replacer, text)
    
    # 2. 按行拆分
    lines = text_safe.split('\n')
    
    results = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 3. 还原代码块
        if "__CODE_BLOCK_" in line:
            placeholders = re.findall(r"__CODE_BLOCK_(\d+)__", line)
            for idx_str in placeholders:
                idx = int(idx_str)
                original_code = code_blocks[idx]
                line = line.replace(f"__CODE_BLOCK_{idx}__", original_code)
        
        results.append(line)
        
    return results
