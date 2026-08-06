"""统一发送队列 - 每 Bot 一个队列，Round-Robin 用户公平调度

架构:
  Handler 层 → 拆分文件为批次 → submit 到队列 → 等待完成
  队列消费者 → Round-Robin 取各用户的批次 → 调 send_batch → 固定间隔

持久化:
  配置 REDIS_URL 时，任务同时写入 Redis，发送成功后移除
  进程重启时从 Redis 恢复未完成任务
  未配置 Redis 时纯内存，零影响
"""
import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from typing import List, Dict, Optional

from config import SEND_BATCH_DELAY, SEND_MIN_INTERVAL

logger = logging.getLogger(__name__)


# ===== 全局注册表 =====
_queues: Dict[str, "SendQueue"] = {}


def get_queue(bot_name: str) -> "SendQueue":
    """获取或创建某 Bot 的发送队列"""
    if bot_name not in _queues:
        q = SendQueue(bot_name)
        _queues[bot_name] = q
        q.start()
        logger.info("SendQueue(@%s): 已创建并启动", bot_name)
    return _queues[bot_name]


def get_queue_from_context(context) -> "SendQueue":
    """从 context 获取当前 Bot 的发送队列"""
    bot_name = getattr(context.bot, 'username', 'unknown')
    q = get_queue(bot_name)
    q.set_bot(context.bot)
    return q


async def stop_all_queues():
    """停止所有队列消费者（进程退出时调用）"""
    for q in _queues.values():
        await q.stop()
    _queues.clear()


# ===== 任务 =====

class SendTask:
    """一个发送批次"""
    __slots__ = ('task_id', 'chat_id', 'files', 'caption', 'future', 'auto_id', 'protect_content', 'auto_delete')

    def __init__(self, chat_id: int, files: List[Dict], caption: str = "",
                 auto_id: str = None, task_id: str = None,
                 protect_content: bool = False, auto_delete: int = 0):
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.chat_id = chat_id
        self.files = files
        self.caption = caption
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.auto_id = auto_id  # 用于 auto_send 取消
        self.protect_content = protect_content  # 转发保护（VIP 功能）
        self.auto_delete = auto_delete  # 自动删除延迟秒数（VIP 功能）

    def to_json(self) -> str:
        """序列化为 JSON（用于 Redis 持久化）"""
        return json.dumps({
            'id': self.task_id,
            'chat_id': self.chat_id,
            'files': self.files,
            'caption': self.caption,
            'auto_id': self.auto_id,
            'protect_content': self.protect_content,
            'auto_delete': self.auto_delete,
        }, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, data: str) -> "SendTask":
        """从 JSON 反序列化"""
        d = json.loads(data)
        return cls(
            chat_id=d['chat_id'],
            files=d['files'],
            caption=d.get('caption', ''),
            auto_id=d.get('auto_id'),
            task_id=d.get('id'),
            protect_content=d.get('protect_content', False),
            auto_delete=d.get('auto_delete', 0),
        )


# ===== 队列 =====

class SendQueue:
    """每 Bot 的发送队列，Round-Robin 用户公平调度"""

    def __init__(self, bot_name: str):
        self.bot_name = bot_name
        self._bot = None
        self._queues: OrderedDict[int, List[SendTask]] = OrderedDict()
        self._running = False
        self._consumer_task: Optional[asyncio.Task] = None
        self._event = asyncio.Event()
        self._last_send = 0.0
        self._total_sent = 0
        self._rate_limit_until = 0.0  # Bot 级别限流截止时间（monotonic）
        self._redis = None  # 延迟获取 RedisManager
        self._cancelled_chats: set = set()  # 已取消的 chat_id 集合
        self._current_chat_id: Optional[int] = None
        self._current_task: Optional[SendTask] = None
        self._current_send_task: Optional[asyncio.Task] = None
        self._bg_tasks: set = set()  # 跟踪 fire-and-forget 后台 task，防止被 GC 回收

    @property
    def _redis_key(self) -> str:
        """Redis 队列 key"""
        return f"sq:{self.bot_name}"

    async def _get_redis(self):
        """获取 Redis 实例（延迟导入）"""
        if self._redis is None:
            try:
                from redis_manager import get_redis
                r = await get_redis()
                if r.available:
                    self._redis = r
                else:
                    self._redis = False  # 标记不可用，不再重试
            except Exception:
                self._redis = False
        return self._redis if self._redis is not False else None

    def _spawn_background(self, coro) -> asyncio.Task:
        """安全地启动 fire-and-forget 后台 task

        asyncio.create_task 返回的 task 若不保留引用，可能被 Python GC 提前回收
        （官方文档明确警告）。这里用集合跟踪，完成后自动移除。
        """
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)
        return t

    async def _persist_task(self, task: SendTask):
        """将任务持久化到 Redis"""
        r = await self._get_redis()
        if r:
            try:
                await r.queue_push(self._redis_key, task.to_json())
            except Exception as e:
                logger.warning("SendQueue(@%s): Redis 持久化失败: %s", self.bot_name, e)

    async def _remove_task(self, task: SendTask):
        """从 Redis 移除已完成的任务"""
        r = await self._get_redis()
        if r:
            try:
                # LREM count=1 删除第一个匹配的元素
                await r._redis.lrem(self._redis_key, 1, task.to_json())
            except Exception as e:
                logger.warning("SendQueue(@%s): Redis 移除任务失败: %s", self.bot_name, e)

    async def _restore_from_redis(self):
        """从 Redis 恢复未完成的任务（启动时调用）"""
        r = await self._get_redis()
        if not r:
            return 0

        try:
            # 获取所有待处理任务
            key = self._redis_key
            items = await r._redis.lrange(key, 0, -1)
            if not items:
                return 0

            # 清空 Redis 队列（恢复后会重新持久化）
            await r._redis.delete(key)

            restored = 0
            for item in items:
                try:
                    task = SendTask.from_json(item)
                    if task.chat_id not in self._queues:
                        self._queues[task.chat_id] = []
                    self._queues[task.chat_id].append(task)
                    restored += 1
                except Exception as e:
                    logger.warning("SendQueue(@%s): 恢复任务失败: %s", self.bot_name, e)

            if restored > 0:
                logger.info("SendQueue(@%s): 从 Redis 恢复 %d 个任务", self.bot_name, restored)
                # 重新持久化到 Redis（干净的列表）
                for tasks in self._queues.values():
                    for t in tasks:
                        await r.queue_push(key, t.to_json())
                self._event.set()

            return restored
        except Exception as e:
            logger.warning("SendQueue(@%s): Redis 恢复失败: %s", self.bot_name, e)
            return 0

    def set_bot(self, bot):
        """设置 bot 实例（用于发送）"""
        self._bot = bot

    def start(self):
        """启动消费者"""
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_loop())

    async def stop(self):
        """停止消费者"""
        self._running = False
        self._event.set()
        # 取消所有等待中的任务
        for tasks in self._queues.values():
            for t in tasks:
                if not t.future.done():
                    t.future.cancel()
        self._queues.clear()
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    @property
    def pending(self) -> int:
        """队列中待处理任务总数"""
        return sum(len(v) for v in self._queues.values())

    def queue_info(self, chat_id: int) -> Dict:
        """获取指定用户的队列状态"""
        user_pending = len(self._queues.get(chat_id, []))
        total_pending = self.pending
        position = 0
        for cid, tasks in self._queues.items():
            if cid == chat_id:
                break
            position += len(tasks)
        return {
            'user_pending': user_pending,
            'total_pending': total_pending,
            'position': position + 1 if user_pending > 0 else 0,
        }

    # ===== 提交方式 =====

    async def submit_batch(self, chat_id: int, files: List[Dict],
                           caption: str = "", auto_id: str = None,
                           protect_content: bool = False, auto_delete: int = 0) -> int:
        """提交一个批次并等待完成。返回成功发送数量。"""
        task = SendTask(chat_id, files, caption, auto_id,
                        protect_content=protect_content, auto_delete=auto_delete)
        if chat_id not in self._queues:
            self._queues[chat_id] = []
        self._queues[chat_id].append(task)
        await self._persist_task(task)
        self._event.set()
        return await task.future

    def submit_batch_async(self, chat_id: int, files: List[Dict],
                           caption: str = "", auto_id: str = None,
                           protect_content: bool = False, auto_delete: int = 0) -> SendTask:
        """提交一个批次但不等待。返回 SendTask（可通过 await task.future 获取结果）。"""
        task = SendTask(chat_id, files, caption, auto_id,
                        protect_content=protect_content, auto_delete=auto_delete)
        if chat_id not in self._queues:
            self._queues[chat_id] = []
        self._queues[chat_id].append(task)
        # 异步持久化
        self._spawn_background(self._persist_task(task))
        self._event.set()
        return task

    def cancel_auto(self, chat_id: int, auto_id: str) -> int:
        """取消指定用户的 auto_send 任务"""
        tasks = self._queues.get(chat_id, [])
        removed = 0
        for t in tasks[:]:
            if t.auto_id == auto_id:
                if not t.future.done():
                    t.future.cancel()
                tasks.remove(t)
                removed += 1
                # 异步从 Redis 移除
                self._spawn_background(self._remove_task(t))
        if not tasks and chat_id in self._queues:
            del self._queues[chat_id]
        if removed:
            logger.info("SendQueue(@%s): 取消 %d 个 auto_send 任务 (chat_id=%s, auto_id=%s)",
                        self.bot_name, removed, chat_id, auto_id)
        return removed

    def cancel_chat(self, chat_id: int) -> int:
        """取消指定用户的所有队列任务（立即生效）

        同时标记 chat_id，让消费者正在处理的该用户任务也被取消。
        返回被取消的任务数。
        """
        self._cancelled_chats.add(chat_id)
        stopped = self._drain_chat(chat_id, cancel_futures=True, include_current=True,
                                   log_prefix="cancel_chat")
        # 唤醒消费者（队列可能变空，需要重新检查）
        self._event.set()
        return stopped

    def is_chat_cancelled(self, chat_id: int) -> bool:
        """检查 chat_id 是否已被标记为取消"""
        return chat_id in self._cancelled_chats

    def clear_chat_cancel(self, chat_id: int):
        """清除取消标记（开始新的发送时调用）"""
        self._cancelled_chats.discard(chat_id)

    def clear_chat_pending(self, chat_id: int) -> int:
        """清除指定用户的所有待处理任务（不标记取消）

        用于 _auto_send / _send_paginated 开始前清除 Redis 恢复的旧任务，
        避免旧任务 + 新提交重复发送。
        """
        return self._drain_chat(chat_id, cancel_futures=True, log_prefix="clear_chat_pending")

    def _drain_chat(self, chat_id: int, cancel_futures: bool = True,
                    include_current: bool = False, log_prefix: str = "drain") -> int:
        """统一清理某 chat 的排队任务（消费者内部复用，消除多处重复代码）

        - 仅清理 self._queues 中该 chat 的待处理任务
        - include_current=True 时同时取消正在发送的 _current_send_task
        返回被取消的任务数
        """
        stopped = 0
        if include_current and self._current_chat_id == chat_id:
            if self._current_send_task and not self._current_send_task.done():
                self._current_send_task.cancel()
                logger.info("SendQueue(@%s): %s 取消正在发送的 chat_id=%s",
                            self.bot_name, log_prefix, chat_id)
            if self._current_task and not self._current_task.future.done():
                self._current_task.future.cancel()
            stopped += 1

        tasks = self._queues.pop(chat_id, [])
        for t in tasks:
            if cancel_futures and not t.future.done():
                t.future.cancel()
                stopped += 1
            self._spawn_background(self._remove_task(t))

        if stopped:
            logger.info("SendQueue(@%s): %s chat_id=%s 清理 %d 个任务",
                        self.bot_name, log_prefix, chat_id, stopped)
        return stopped

    # ===== 消费者 =====

    async def _consume_loop(self):
        """消费者主循环：Round-Robin 从各用户取任务

        优化点：用 OrderedDict 的 popitem(last=False) + move_to_end 实现真正的
        轮转调度，避免每轮 list(self._queues.keys()) 全量复制（队列大时开销显著）。
        每轮处理本批次就绪的所有 chat（队列快照期），逐个消费队首。
        """
        from senders import send_batch

        # 启动时从 Redis 恢复未完成任务
        await self._restore_from_redis()

        while self._running:
            # 等待任务
            if not self._queues:
                self._event.clear()
                await self._event.wait()
                if not self._running:
                    break

            # Bot 级别限流检查：任何用户触发限流后，整个 Bot 队列暂停
            now = time.monotonic()
            if self._rate_limit_until > now:
                wait = self._rate_limit_until - now
                logger.info("SendQueue(@%s): Bot 限流中，等待 %.0fs (剩余队列=%d)",
                            self.bot_name, wait, self.pending)
                await asyncio.sleep(wait)

            # 本轮要处理的 chat 数量快照：只消费开始时已排队的用户，
            # 新提交的 chat 留到下一轮，保证本轮能在有限步内结束。
            batch_count = len(self._queues)
            processed_any = False

            for _ in range(batch_count):
                if not self._running or not self._queues:
                    break

                # 取队首（O(1)，无需复制全部 keys）
                chat_id, tasks = self._queues.popitem(last=False)

                # 该 chat 已被 /stop 取消：清理后跳过
                if chat_id in self._cancelled_chats:
                    self._cancelled_chats.discard(chat_id)
                    for t in tasks:
                        if not t.future.done():
                            t.future.cancel()
                        self._spawn_background(self._remove_task(t))
                    logger.info("SendQueue(@%s): 消费者跳过已取消的 chat_id=%s (%d 个任务)",
                                self.bot_name, chat_id, len(tasks))
                    continue

                if not tasks:
                    continue

                task = tasks[0]
                cancelled_now = chat_id in self._cancelled_chats

                # 如果该 chat 还有后续任务，先放回队尾（保证其它用户公平）
                if len(tasks) > 1 and not cancelled_now:
                    self._queues[chat_id] = tasks[1:]
                    self._queues.move_to_end(chat_id)
                else:
                    # 只剩这一个任务（或已取消），不需要放回
                    if cancelled_now:
                        # 整组连同当前任务一起清理
                        self._cancelled_chats.discard(chat_id)
                        for t in tasks:
                            if not t.future.done():
                                t.future.cancel()
                            self._spawn_background(self._remove_task(t))
                        logger.info("SendQueue(@%s): 消费者取消正在处理的 chat_id=%s 任务",
                                    self.bot_name, chat_id)
                        continue

                processed_any = True

                # 最小发送间隔
                now = time.monotonic()
                wait = SEND_MIN_INTERVAL - (now - self._last_send)
                if wait > 0:
                    await asyncio.sleep(wait)

                # 发送前最终检查：可能在等待间隔期间被 /stop 取消
                if chat_id in self._cancelled_chats:
                    self._drain_chat(chat_id, cancel_futures=True, log_prefix="发送前最终检查取消")
                    self._cancelled_chats.discard(chat_id)
                    if not task.future.done():
                        task.future.cancel()
                    self._spawn_background(self._remove_task(task))
                    continue

                # 发送
                self._current_chat_id = chat_id
                self._current_task = task
                self._current_send_task = asyncio.create_task(
                    send_batch(self._bot, task.chat_id, task.files, task.caption,
                               protect_content=task.protect_content,
                               auto_delete=task.auto_delete)
                )
                try:
                    sent = await self._current_send_task
                except asyncio.CancelledError:
                    logger.info("SendQueue(@%s): chat_id=%s 当前发送被取消", self.bot_name, task.chat_id)
                    if not task.future.done():
                        task.future.cancel()
                    await self._remove_task(task)
                    continue
                except Exception as e:
                    from telegram.error import RetryAfter
                    from senders import SendBlockedError

                    if isinstance(e, RetryAfter):
                        # Bot 级别限流：设置整个队列的暂停时间
                        wait = (e.retry_after if hasattr(e, 'retry_after') and e.retry_after else 30) + 3
                        self._rate_limit_until = time.monotonic() + wait
                        logger.warning("SendQueue(@%s): Bot 限流 %.0fs，暂停整个队列 (触发者 chat_id=%s, 剩余队列=%d)",
                                       self.bot_name, wait, task.chat_id, self.pending)
                        # 将任务放回该 chat 队列头部（Redis 中不移除）
                        existing = self._queues.get(chat_id)
                        if existing is None:
                            self._queues[chat_id] = [task]
                            self._queues.move_to_end(chat_id, last=False)  # 限流重试者优先
                        else:
                            existing.insert(0, task)
                            self._queues.move_to_end(chat_id, last=False)
                        break  # 跳出本轮，整个 Bot 队列等待
                    elif isinstance(e, SendBlockedError):
                        # 用户已拉黑 Bot：取消该用户所有剩余任务
                        cancelled = self._drain_chat(chat_id, cancel_futures=False, log_prefix="拉黑Bot取消剩余")
                        logger.warning("SendQueue(@%s): chat_id=%s 已拉黑 Bot，取消剩余 %d 个任务",
                                       self.bot_name, chat_id, cancelled)
                        if not task.future.done():
                            task.future.set_exception(e)
                        await self._remove_task(task)
                    else:
                        logger.error("SendQueue(@%s): 发送失败 chat_id=%s: %s",
                                     self.bot_name, task.chat_id, e)
                        if not task.future.done():
                            task.future.set_exception(e)
                        # 非限流失败也从 Redis 移除（避免重启后反复重试）
                        await self._remove_task(task)
                else:
                    self._last_send = time.monotonic()
                    self._total_sent += sent
                    if not task.future.done():
                        task.future.set_result(sent)
                    # 发送成功，从 Redis 移除
                    await self._remove_task(task)
                finally:
                    self._current_chat_id = None
                    self._current_task = None
                    self._current_send_task = None

                # 批次间固定间隔
                await asyncio.sleep(SEND_BATCH_DELAY)

            if not processed_any:
                await asyncio.sleep(0.05)


# ===== 辅助函数：按类型拆分文件为批次 =====

def split_files_to_batches(files: List[Dict], batch_size: int = None) -> List[List[Dict]]:
    """按类型分组后拆成批次，同类型文件在一起（可合并为相册）
    
    顺序: 图片/视频 → 文档 → 音频
    返回: [[batch1_files], [batch2_files], ...]
    """
    from config import GROUP_SEND_SIZE
    size = batch_size or GROUP_SEND_SIZE

    # 单次遍历分组，避免对 files 三次列表推导
    photo_video = []
    documents = []
    audios = []
    for f in files:
        ft = f['file_type']
        if ft in ('photo', 'video'):
            photo_video.append(f)
        elif ft in ('document', 'voice'):
            documents.append(f)
        elif ft == 'audio':
            audios.append(f)

    batches = []
    for group in [photo_video, documents, audios]:
        for i in range(0, len(group), size):
            batches.append(group[i:i + size])

    return batches