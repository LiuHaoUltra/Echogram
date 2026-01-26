import re

def split_message(text: str) -> list[str]:
    """
    智能拆分消息：
    1. 按换行符拆分，但保留 Markdown 代码块 (```...```) 的完整性。
    2. 如果代码块内部有换行，不拆分。
    3. 过滤掉空行。
    """
    if not text:
        return []

    # 1. 提取代码块
    # 使用正则查找 ```...```, 注意非贪婪匹配和 DOTALL
    # 我们先将代码块替换为占位符，拆分完后再还原
    
    code_blocks = []
    
    def replacer(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    # 正则：匹配 ```code``` 块
    pattern = re.compile(r"```.*?```", re.DOTALL)
    
    # 替换代码块为占位符
    text_safe = pattern.sub(replacer, text)
    
    # 2. 按换行符拆分
    lines = text_safe.split('\n')
    
    results = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # 3. 还原代码块
        # 可能一行里有多个占位符? (通常不会，因为 ``` 也是独占一行的多)
        # 但为了保险，循环替换
        if "__CODE_BLOCK_" in line:
            # 找到所有占位符
            placeholders = re.findall(r"__CODE_BLOCK_(\d+)__", line)
            for idx_str in placeholders:
                idx = int(idx_str)
                original_code = code_blocks[idx]
                line = line.replace(f"__CODE_BLOCK_{idx}__", original_code)
        
        results.append(line)
        
    return results
