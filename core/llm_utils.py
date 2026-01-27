from openai import AsyncOpenAI
from core.config_service import config_service
from utils.logger import logger
import json

async def fetch_available_models():
    """
    尝试从配置的 API 获取模型列表
    返回: (success: bool, data: list[str] | str)
    - 成功返回模型 ID 列表
    - 失败返回错误信息字符串
    """
    configs = await config_service.get_all_settings()
    api_key = configs.get("api_key")
    base_url = configs.get("api_base_url")

    if not api_key:
        return False, "API Key 未配置"

    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        # 设置较短超时，避免卡住界面
        models_page = await client.models.list(timeout=10.0)
        
        # 提取模型 ID 并排序
        model_ids = sorted([m.id for m in models_page.data])
        return True, model_ids
        
    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
        return False, str(e)

async def simple_chat(model: str, messages: list, temperature: float = 0.7) -> str:
    """
    通用 LLM 调用接口 (用于后台任务如总结)
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
            temperature=temperature
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""
