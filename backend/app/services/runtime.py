import json
from collections import defaultdict
from secrets import token_urlsafe
from time import monotonic

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings


class RuntimeStateService:
    """Redis-backed ephemeral state with an in-process development fallback."""

    def __init__(self) -> None:
        settings = get_settings()
        self._redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        self._cancelled: set[str] = set()
        self._rate_windows: dict[str, list[float]] = defaultdict(list)
        self._unavailable_until = 0.0

    async def status(self) -> str:
        if monotonic() < self._unavailable_until:
            return "fallback"
        try:
            return "connected" if await self._redis.ping() else "fallback"
        except RedisError:
            self._mark_unavailable()
            return "fallback"

    async def set_cancelled(self, session_id: str) -> None:
        self._cancelled.add(session_id)
        try:
            await self._redis.set(f"chat:cancel:{session_id}", "1", ex=600)
        except RedisError:
            self._mark_unavailable()

    async def clear_cancelled(self, session_id: str) -> None:
        self._cancelled.discard(session_id)
        try:
            await self._redis.delete(f"chat:cancel:{session_id}")
        except RedisError:
            self._mark_unavailable()

    async def is_cancelled(self, session_id: str) -> bool:
        if session_id in self._cancelled:
            return True
        try:
            return bool(await self._redis.exists(f"chat:cancel:{session_id}"))
        except RedisError:
            self._mark_unavailable()
            return False

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        redis_key = f"rate:{key}"
        try:
            count = await self._redis.incr(redis_key)
            if count == 1:
                await self._redis.expire(redis_key, window_seconds)
            return count <= limit
        except RedisError:
            self._mark_unavailable()
            now = monotonic()
            window = self._rate_windows[key]
            window[:] = [timestamp for timestamp in window if now - timestamp < window_seconds]
            if len(window) >= limit:
                return False
            window.append(now)
            return True

    async def get_json(self, key: str) -> dict[str, object] | None:
        try:
            value = await self._redis.get(f"cache:{key}")
            return json.loads(value) if value else None
        except (RedisError, json.JSONDecodeError):
            self._mark_unavailable()
            return None

    async def set_json(self, key: str, value: dict[str, object], ttl: int = 60) -> None:
        try:
            await self._redis.set(f"cache:{key}", json.dumps(value, ensure_ascii=False), ex=ttl)
        except RedisError:
            self._mark_unavailable()

    async def delete_json(self, key: str) -> None:
        try:
            await self._redis.delete(f"cache:{key}")
        except RedisError:
            self._mark_unavailable()

    async def delete_prefix(self, prefix: str) -> None:
        """Delete every cached key whose name starts with the given prefix."""
        pattern = f"cache:{prefix}*"
        try:
            cursor = 0
            while True:
                cursor, batch = await self._redis.scan(cursor=cursor, match=pattern, count=200)
                if batch:
                    await self._redis.delete(*batch)
                if cursor == 0:
                    break
        except RedisError:
            self._mark_unavailable()

    async def acquire_lock(self, key: str, ttl: int = 120) -> str | None:
        token = token_urlsafe(18)
        try:
            acquired = await self._redis.set(f"lock:{key}", token, ex=ttl, nx=True)
            return token if acquired else None
        except RedisError:
            self._mark_unavailable()
            return token

    async def release_lock(self, key: str, token: str) -> None:
        try:
            current = await self._redis.get(f"lock:{key}")
            if current == token:
                await self._redis.delete(f"lock:{key}")
        except RedisError:
            self._mark_unavailable()

    async def close(self) -> None:
        await self._redis.aclose()

    # ── SSE event log for reconnection ──────────────────────────────

    async def next_event_id(self, session_id: str) -> str:
        """Atomically generate the next sequential event ID for a chat session."""
        try:
            seq = await self._redis.incr(f"chat:seq:{session_id}")
            await self._redis.expire(f"chat:seq:{session_id}", 3600)
            return f"{session_id}:{seq}"
        except RedisError:
            self._mark_unavailable()
            return f"{session_id}:{int(monotonic() * 1_000_000)}"

    async def append_event(
        self, session_id: str, event_id: str, event_type: str, data: dict[str, object]
    ) -> None:
        """Append an SSE event to the Redis-backed log for later replay."""
        record = json.dumps(
            {"id": event_id, "event": event_type, "data": data}, ensure_ascii=False
        )
        try:
            key = f"chat:events:{session_id}"
            await self._redis.rpush(key, record)
            await self._redis.expire(key, 3600)
        except RedisError:
            self._mark_unavailable()

    async def get_events_since(
        self, session_id: str, last_event_id: str | None
    ) -> list[dict[str, object]]:
        """Return all stored events with IDs strictly after *last_event_id*."""
        try:
            key = f"chat:events:{session_id}"
            records = await self._redis.lrange(key, 0, -1)
            events = [json.loads(record) for record in records]
        except (RedisError, json.JSONDecodeError):
            self._mark_unavailable()
            return []
        if last_event_id is None:
            return events
        result: list[dict[str, object]] = []
        found = False
        for event in events:
            if found:
                result.append(event)
            elif event.get("id") == last_event_id:
                found = True
        # If last_event_id not found (expired or different turn), return everything.
        return result if found else events

    async def set_turn_status(self, session_id: str, status: str) -> None:
        try:
            await self._redis.set(f"chat:status:{session_id}", status, ex=3600)
        except RedisError:
            self._mark_unavailable()

    async def get_turn_status(self, session_id: str) -> str | None:
        try:
            value = await self._redis.get(f"chat:status:{session_id}")
        except RedisError:
            self._mark_unavailable()
            return None
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else value

    async def clear_turn(self, session_id: str) -> None:
        """Clear event log, sequence counter and status for a chat turn."""
        try:
            await self._redis.delete(
                f"chat:events:{session_id}",
                f"chat:seq:{session_id}",
                f"chat:status:{session_id}",
            )
        except RedisError:
            self._mark_unavailable()

    # ── 统一幂等键 API ────────────────────────────────────────────────

    async def get_idempotent(self, key: str) -> dict[str, object] | None:
        """返回幂等键已缓存的结果；不存在或 Redis 不可用时返回 None。"""
        try:
            value = await self._redis.get(f"idem:{key}")
            return json.loads(value) if value else None
        except (RedisError, json.JSONDecodeError):
            self._mark_unavailable()
            return None

    async def set_idempotent(
        self, key: str, result: dict[str, object], ttl: int = 86400
    ) -> None:
        """缓存幂等键对应的最终结果，默认保留 24 小时。"""
        try:
            await self._redis.set(
                f"idem:{key}", json.dumps(result, ensure_ascii=False), ex=ttl
            )
            await self._redis.delete(f"idem:lock:{key}")
        except RedisError:
            self._mark_unavailable()

    async def acquire_idempotency(self, key: str, ttl: int = 300) -> str | None:
        """尝试占用幂等键的处理槽。

        返回令牌表示抢占成功（调用方应执行业务并最终调用
        :meth:`set_idempotent`）；返回 ``None`` 表示已有其他请求正在处理，
        调用方应短暂轮询 :meth:`get_idempotent` 等待结果。
        """
        token = token_urlsafe(18)
        try:
            acquired = await self._redis.set(f"idem:lock:{key}", token, ex=ttl, nx=True)
            return token if acquired else None
        except RedisError:
            self._mark_unavailable()
            return token

    # ── Celery 队列监控辅助 ──────────────────────────────────────────

    async def get_queue_depth(self, queue: str) -> int:
        """返回指定队列的待处理消息数（Redis broker 的 list 长度）。"""
        try:
            return await self._redis.llen(queue)
        except RedisError:
            self._mark_unavailable()
            return 0

    def _mark_unavailable(self) -> None:
        self._unavailable_until = monotonic() + 5


runtime_state = RuntimeStateService()
