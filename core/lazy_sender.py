import asyncio
import time
from typing import Dict, Any, Optional
from telegram.ext import ContextTypes
from core.config_service import config_service
from utils.logger import logger
class LazySender:
    def __init__(self):
        """
        消息聚合发送器 (防抖 + 最大延迟)
        """
        # Buffer 结构: {chat_id: {'task': Task, 'start_time': float, 'context': Context}}
        self.buffers: Dict[int, Dict[str, Any]] = {}
        # Callback 稍后绑定
        self.callback = None
        self._default_max_wait = 60.0

    def set_callback(self, callback):
        """设置刷新时的回调函数"""
        self.callback = callback

    async def on_message(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """
        接收新消息信号
        """
        current_time = time.time()
        
        # 读取防抖配置
        idle_wait_str = await config_service.get_value("aggregation_latency", "10")
        try:
            idle_wait = float(idle_wait_str)
            if idle_wait < 0: idle_wait = 0.5
        except:
            idle_wait = 10.0
            
        # 初始化 Buffer
        if chat_id not in self.buffers:
            self.buffers[chat_id] = {
                'task': None,
                'start_time': current_time,
                'context': context 
            }
        else:
        # 更新 Context
            self.buffers[chat_id]['context'] = context

        buffer = self.buffers[chat_id]

        # 重置计时器 (防抖)
        if buffer['task']:
            buffer['task'].cancel()
            buffer['task'] = None

        # 检查强制发送阈值
        time_elapsed = current_time - buffer['start_time']
        
        if time_elapsed >= self._default_max_wait:
            logger.info(f"LazySender: Max wait reached for Chat {chat_id}, flushing now.")
            await self._flush(chat_id)
            return

        # 开启新计时器
        buffer['task'] = asyncio.create_task(self._wait_and_flush(chat_id, idle_wait))
        logger.debug(f"LazySender: Scheduled flush for Chat {chat_id} in {idle_wait}s")

    async def _wait_and_flush(self, chat_id: int, delay: float):
        """等待静默时间结束，然后发送"""
        try:
            await asyncio.sleep(delay)
            await self._flush(chat_id)
        except asyncio.CancelledError:
            # 被新消息打断
            pass

    async def _flush(self, chat_id: int):
        """执行发送回调"""
        if chat_id not in self.buffers:
            return

        buffer = self.buffers.pop(chat_id) # 清理 Buffer
        context = buffer['context']
        
        if self.callback:
            try:
                await self.callback(chat_id, context)
            except Exception as e:
                logger.error(f"LazySender Callback Error for {chat_id}: {e}")

lazy_sender = LazySender()
