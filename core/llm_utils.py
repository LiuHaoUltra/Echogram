from openai import AsyncOpenAI
from core.config_service import config_service
from utils.logger import logger
import json

async def fetch_available_models():
    """
    获模型列表
    返回: (success, model_ids)
    """
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")

    if not api_key:
        return False, "API Key 未配置"

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        # 10s 超时防止阻塞
        models_page = await client.models.list(timeout=10.0)
        
        # 排序模型 ID
        model_ids = sorted([m.id for m in models_page.data])
        return True, model_ids
        
    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
        return False, str(e)

async def fetch_embedding_models():
    """
    获取专门的 Embedding 模型列表
    Endpoint: GET /embeddings/models (OpenRouter Specific)
    返回: (success, model_ids)
    """
    import httpx
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")

    if not api_key:
        return False, "API Key 未配置"

    # 构造请求地址: base_url 通常是 https://openrouter.ai/api/v1
    # 我们需要拼接 /embeddings/models
    # 如果 base_url 结尾有 /，去掉
    base_url = base_url.rstrip("/")
    target_url = getattr(settings, "OPENROUTER_EMBEDDINGS_URL", f"{base_url}/embeddings/models")
    
    # 防止 base_url 是 generic openai endpoint (如 api.openai.com) 导致 404
    # 用户给的文档是 OpenRouter 特有的。如果用户用的是官方 OpenAI，这里会失败。
    # 策略：如果失败，回退到 fetch_available_models 并尝试过滤 (虽然后者可能也不准)
    # 但针对 OpenRouter，我们优先尝试专用端点。

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/LiuHaoUltra/Echogram", 
            "X-Title": "Echogram"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(target_url, headers=headers)
            
            if resp.status_code == 200:
                data = resp.json()
                # 预期格式: {"data": [{"id": ...}, ...]}
                if "data" in data:
                    model_ids = sorted([m["id"] for m in data["data"]])
                    return True, model_ids
            
            # 如果非 200 (可能是 OpenAI 官方)，我们可以尝试 fallback
            logger.warning(f"Embedding specific fetch failed: {resp.status_code}")
            return False, f"HTTP {resp.status_code}"

    except Exception as e:
        logger.error(f"Failed to fetch embedding models: {e}")
        return False, str(e)

async def simple_chat(model: str, messages: list, temperature: float = 0.7, max_tokens: int = 2000) -> str:
    """
    通用 LLM 调用接口
    """
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")

    if not api_key:
        logger.error("API Key not set for simple_chat")
        return ""

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""
