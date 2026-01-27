import re

def split_message(text: str) -> list[str]:
    """
    智能拆分消息 (保护代码块)
    """
    if not text:
        return []

    # 1. 提取代码块
    # 正则提取代码块
    # 替换为占位符
    
    code_blocks = []
    
    def replacer(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    # 正则：匹配 ```code``` 块
    pattern = re.compile(r"```.*?```", re.DOTALL)
    
    # 替换代码块为占位符
    text_safe = pattern.sub(replacer, text)
    
    # 2. 按换行拆分
    lines = text_safe.split('\n')
    
    results = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 3. 还原代码块
        # 循环替换以防万一
        if "__CODE_BLOCK_" in line:
            # 找到所有占位符
            placeholders = re.findall(r"__CODE_BLOCK_(\d+)__", line)
            for idx_str in placeholders:
                idx = int(idx_str)
                original_code = code_blocks[idx]
                line = line.replace(f"__CODE_BLOCK_{idx}__", original_code)
        
        results.append(line)
        
    return results
