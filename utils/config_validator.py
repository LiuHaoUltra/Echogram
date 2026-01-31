"""配置值安全转换工具"""

from typing import Union


def safe_int_config(value: Union[str, int, None], default: int, min_val: int = None, max_val: int = None) -> int:
    """
    安全地将配置值转为整数，带范围限制
    :param value: 配置值（字符串或整数）
    :param default: 默认值
    :param min_val: 最小值限制
    :param max_val: 最大值限制
    :return: 验证后的整数值
    """
    try:
        result = int(value) if value is not None else default
        if min_val is not None:
            result = max(min_val, result)
        if max_val is not None:
            result = min(max_val, result)
        return result
    except (ValueError, TypeError):
        return default


def safe_float_config(value: Union[str, float, None], default: float, min_val: float = None, max_val: float = None) -> float:
    """
    安全地将配置值转为浮点数，带范围限制
    :param value: 配置值（字符串或浮点数）
    :param default: 默认值
    :param min_val: 最小值限制
    :param max_val: 最大值限制
    :return: 验证后的浮点数值
    """
    try:
        result = float(value) if value is not None else default
        if min_val is not None:
            result = max(min_val, result)
        if max_val is not None:
            result = min(max_val, result)
        return result
    except (ValueError, TypeError):
        return default
