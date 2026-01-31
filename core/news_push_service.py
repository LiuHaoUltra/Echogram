import asyncio
import random
from datetime import datetime, time, timezone
from telegram.ext import ContextTypes
from sqlalchemy import select, update

from config.database import get_db_session
from config.settings import settings
from models.news import NewsSubscription, ChatSubscription
from core.news_service import NewsService
from core.access_service import access_service
from core.history_service import history_service
from core.llm_utils import simple_chat
from utils.logger import logger
from core.sender_service import sender_service # 修复：移动到全局作用域

class NewsPushService:
    """
    新闻主动推送服务 (NewsPush)
    周期性唤醒 -> 感知(RSS) -> 过滤(Filter) -> 按需分发(Dispatch) -> 生成(Speaker) -> 推送
    """

    async def run_push_loop(self, context: ContextTypes.DEFAULT_TYPE, force: bool = False):
        """
        核心循环入口
        Logic: Loop Subs -> Fetch -> Global Filter -> Loop Linked Chats -> Speak -> Send
        """
        logger.info(f"NewsPush: Waking up... (Force={force})")

        # 1. 环境检查
        if not force and not await self._is_active_hours():
            logger.info("NewsPush: Sleeping hours. Going back to sleep.")
            return

        # 2. 获取所有启用的订阅源
        subscriptions = []
        async for session in get_db_session():
            stmt = select(NewsSubscription).where(NewsSubscription.is_active == True)
            result = await session.execute(stmt)
            subscriptions = result.scalars().all()
            # 这里的 scalars() 会执行查询并将结果存入内存

        if not subscriptions:
            logger.info("NewsPush: No active subscriptions.")
            return

        for sub in subscriptions:
            # --- Per Subscription Process ---
            try:
                # A. Fetch
                last_time = sub.last_publish_time
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)

                fetched_items = await NewsService.fetch_new_items(sub.route, last_time)
                
                # B. Update Status
                await self._update_sub_status(sub.id, "normal", error=None)
                
                if not fetched_items:
                    # 只有在非强制模式下才静默，强制模式增加日志
                    if force:
                        logger.info(f"NewsPush: No new items found for {sub.name} (Last: {last_time})")
                    continue
                    
                logger.info(f"NewsPush: Found {len(fetched_items)} new items from {sub.name}")
                
                # C. Process Items
                # 随机取 1 条避免刷屏
                target_item = random.choice(fetched_items)
                
                # D. Global Filter (Step 1)
                should_pass = await self._filter_news_global(target_item)
                if not should_pass:
                    logger.info(f"NewsPush: '{target_item['title']}' filtered out by Global Filter.")
                    # 如果被过滤，我们依然更新时间戳，否则会卡在这里不断尝试该条
                    latest_item_time = max([x['date_published'] for x in fetched_items])
                    naive_latest = latest_item_time.astimezone(timezone.utc).replace(tzinfo=None)
                    await self._update_sub_last_publish(sub.id, naive_latest)
                    continue
                    
                # E. Dispatch to Linked Chats
                linked_chat_ids = await self._get_linked_chats(sub.id)
                if not linked_chat_ids:
                    logger.info(f"NewsPush: No linked chats for {sub.name}.")
                    continue
                
                sent_any = False
                for chat_id in linked_chat_ids:
                    # F. Chat Idle Check
                    if not force and not await self._is_chat_idle(chat_id):
                        logger.info(f"NewsPush: Chat {chat_id} is busy. Skipping.")
                        continue
                        
                    # G. Speaker (Step 2)
                    speech = await self._generate_speech(sub.name, target_item, chat_id)
                    if speech:
                        logger.info(f"NewsPush: Decided to share to {chat_id}")
                        await self._act_send(chat_id, speech, context)
                        sent_any = True

                # H. Update Marker
                # 只有在成功生成或强制模式下推演过后，才更新该订阅源的时间戳
                if sent_any or force:
                    latest_item_time = max([x['date_published'] for x in fetched_items])
                    naive_latest = latest_item_time.astimezone(timezone.utc).replace(tzinfo=None)
                    await self._update_sub_last_publish(sub.id, naive_latest)

            except Exception as e:
                logger.error(f"NewsPush Error processing sub {sub.name}: {e}", exc_info=True)
                await self._update_sub_status(sub.id, "error", error=str(e))

    async def _update_sub_status(self, sub_id: int, status: str, error: str = None):
        """更新订阅源监控状态"""
        async for session in get_db_session():
            stmt = select(NewsSubscription).where(NewsSubscription.id == sub_id)
            r = await session.execute(stmt)
            sub = r.scalar_one_or_none()
            if sub:
                sub.status = status
                sub.last_check_time = datetime.utcnow()
                if status == "error":
                    sub.error_count += 1
                    sub.last_error = error
                else:
                    sub.error_count = 0
                    sub.last_error = None
                session.add(sub)
                await session.commit()

    async def _update_sub_last_publish(self, sub_id: int, new_time: datetime):
        """更新最后发布时间"""
        async for session in get_db_session():
            stmt = update(NewsSubscription).where(NewsSubscription.id == sub_id).values(last_publish_time=new_time)
            await session.execute(stmt)
            await session.commit()

    async def _get_linked_chats(self, sub_id: int) -> list[int]:
        """获取订阅了该源的所有 Chat ID"""
        async for session in get_db_session():
            stmt = select(ChatSubscription.chat_id).where(
                ChatSubscription.subscription_id == sub_id,
                ChatSubscription.is_active == True
            )
            result = await session.execute(stmt)
            return result.scalars().all()

    async def add_subscription(self, route: str, name: str, bind_chat_id: int = None) -> bool:
        """添加订阅源，并可选绑定到一个群组"""
        async for session in get_db_session():
            try:
                existing = await session.execute(select(NewsSubscription).where(NewsSubscription.route == route))
                sub = existing.scalar_one_or_none()
                
                if not sub:
                    sub = NewsSubscription(route=route, name=name)
                    session.add(sub)
                    await session.flush()
                
                if bind_chat_id:
                    stmt_bind = select(ChatSubscription).where(
                        ChatSubscription.chat_id == bind_chat_id,
                        ChatSubscription.subscription_id == sub.id
                    )
                    existing_bind = await session.execute(stmt_bind)
                    if not existing_bind.scalar_one_or_none():
                        bind = ChatSubscription(chat_id=bind_chat_id, subscription_id=sub.id)
                        session.add(bind)

                await session.commit()
                return True
            except Exception as e:
                logger.error(f"Failed to add subscription: {e}")
                return False

    async def remove_subscription(self, sub_id: int) -> bool:
        """删除订阅源"""
        from sqlalchemy import delete
        async for session in get_db_session():
            await session.execute(delete(ChatSubscription).where(ChatSubscription.subscription_id == sub_id))
            await session.execute(delete(NewsSubscription).where(NewsSubscription.id == sub_id))
            await session.commit()
            return True
    
    async def get_all_subscriptions(self):
        """获取所有订阅源"""
        async for session in get_db_session():
            result = await session.execute(select(NewsSubscription))
            return result.scalars().all()

    async def _filter_news_global(self, item: dict) -> bool:
        """全局价值过滤"""
        from utils.prompts import prompt_builder
        from core.config_service import config_service
        settings_map = await config_service.get_all_settings()
        summary_model = settings_map.get("summary_model_name") or settings.SUMMARY_MODEL
        msgs = prompt_builder.build_agentic_filter_messages(item['title'], item['content'][:500])
        try:
            resp = await simple_chat(summary_model, msgs, temperature=0.1)
            return "YES" in resp
        except Exception as e:
            logger.error(f"Global Filter Error: {e}")
            return False

    async def _generate_speech(self, source_name: str, item: dict, chat_id: int) -> str:
        """个性的生成"""
        from utils.prompts import prompt_builder
        from core.config_service import config_service
        from core.summary_service import summary_service
        settings_map = await config_service.get_all_settings()
        main_model = settings_map.get("model_name") or settings.OPENAI_MODEL_NAME
        group_memory = await summary_service.get_summary(chat_id)
        memory_context = f"\n[群组长期记忆/上下文]:\n{group_memory}" if group_memory else "\n[群组长期记忆]: 无相关历史."
        sys_prompt_custom = settings_map.get("system_prompt", "")
        # 注意：此处 build_system_prompt 的参数需要确保正确
        full_sys_prompt = prompt_builder.build_system_prompt(soul_prompt=sys_prompt_custom, dynamic_summary="") 
        msgs = prompt_builder.build_agentic_speaker_messages(
            system_prompt=full_sys_prompt,
            source_name=source_name,
            title=item['title'],
            content=item['content'][:500],
            memory_context=memory_context
        )
        try:
            resp = await simple_chat(main_model, msgs, temperature=0.8)
            return resp.strip() if resp else ""
        except Exception as e:
            logger.error(f"Speaker Error: {e}")
            return ""

    async def _is_active_hours(self) -> bool:
        from core.config_service import config_service
        start_str = await config_service.get_value("agentic_active_start", "08:00")
        end_str = await config_service.get_value("agentic_active_end", "23:00")
        try:
            now = datetime.now().time()
            start = datetime.strptime(start_str, "%H:%M").time()
            end = datetime.strptime(end_str, "%H:%M").time()
            if start <= end: return start <= now <= end
            else: return start <= now or now <= end
        except: return 8 <= datetime.now().hour <= 23

    async def _is_chat_idle(self, chat_id: int) -> bool:
        from core.config_service import config_service
        threshold_str = await config_service.get_value("agentic_idle_threshold", "30")
        try: threshold_seconds = int(threshold_str) * 60
        except: threshold_seconds = 1800
        last_time = await history_service.get_last_message_time(chat_id)
        if not last_time: return True 
        if last_time.tzinfo is None: last_time = last_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - last_time).total_seconds() > threshold_seconds

    async def _act_send(self, chat_id: int, content: str, context: ContextTypes.DEFAULT_TYPE):
        try:
            await sender_service.send_llm_reply(
                chat_id=chat_id,
                reply_content=content,
                context=context
            )
        except Exception as e:
            logger.error(f"NewsPush: Failed to send to {chat_id}: {e}", exc_info=True)

news_push_service = NewsPushService()
