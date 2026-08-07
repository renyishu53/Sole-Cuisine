"""日历数据访问（去家庭化版，user 作用域，无参与者）。

原参与者（EventParticipant/FamilyMemberProfile）逻辑已移除，事件冲突改为纯时间重叠判定。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CalendarEventException,
    CalendarEventRecord,
)
from app.schemas import (
    CalendarConflict,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    CalendarOccurrenceExceptionCreate,
    CalendarRecurrenceRule,
)

WEEKDAY_LABELS = [
    "\u5468\u4e00",
    "\u5468\u4e8c",
    "\u5468\u4e09",
    "\u5468\u56db",
    "\u5468\u4e94",
    "\u5468\u516d",
    "\u5468\u65e5",
]


class CalendarRepositoryError(ValueError):
    """Raised when a calendar operation violates user-scoped invariants."""


@dataclass(frozen=True, slots=True)
class CalendarOccurrence:
    event: CalendarEventRecord
    start_at: datetime
    end_at: datetime
    sequence: int = 0
    override: dict[str, object] | None = None

    @property
    def key(self) -> tuple[int, int]:
        return self.event.id, self.sequence


def _comparison_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _storage_datetime(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=None)


def _overlaps(
    start_at: datetime,
    end_at: datetime,
    other_start: datetime,
    other_end: datetime,
) -> bool:
    return _comparison_datetime(start_at) < _comparison_datetime(
        other_end
    ) and _comparison_datetime(end_at) > _comparison_datetime(other_start)


def _default_window() -> tuple[datetime, datetime]:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=30), today + timedelta(days=90)


def _month_delta(origin: date, current: date) -> int:
    return (current.year - origin.year) * 12 + current.month - origin.month


def _make_datetime(day: date, source: datetime) -> datetime:
    return datetime.combine(
        day,
        time(source.hour, source.minute, source.second, source.microsecond),
        tzinfo=source.tzinfo,
    )


def _iter_occurrences(
    *,
    start_at: datetime,
    end_at: datetime,
    recurrence: CalendarRecurrenceRule,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    duration = end_at - start_at
    if recurrence.type == "none":
        return [(start_at, end_at)] if _overlaps(start_at, end_at, window_start, window_end) else []

    generated: list[tuple[datetime, datetime]] = []
    current = start_at.date()
    stop_date = window_end.date() + timedelta(days=1)
    if recurrence.until is not None:
        stop_date = min(stop_date, recurrence.until.date() + timedelta(days=1))
    weekdays = set(recurrence.days_of_week or [start_at.weekday()])
    seen_total = 0
    guard = 0
    while current <= stop_date and guard < 1500:
        guard += 1
        days_since_start = (current - start_at.date()).days
        if days_since_start < 0:
            current += timedelta(days=1)
            continue

        include = False
        if recurrence.type == "daily":
            include = days_since_start % recurrence.interval == 0
        elif recurrence.type == "weekly":
            week_delta = days_since_start // 7
            include = current.weekday() in weekdays and week_delta % recurrence.interval == 0
        elif recurrence.type == "monthly":
            months = _month_delta(start_at.date(), current)
            include = (
                months >= 0 and months % recurrence.interval == 0 and current.day == start_at.day
            )

        if include:
            seen_total += 1
            occurrence_start = _make_datetime(current, start_at)
            occurrence_end = occurrence_start + duration
            if recurrence.count is not None and seen_total > recurrence.count:
                break
            if recurrence.until is not None and _comparison_datetime(
                occurrence_start
            ) > _comparison_datetime(recurrence.until):
                break
            if _overlaps(occurrence_start, occurrence_end, window_start, window_end):
                generated.append((occurrence_start, occurrence_end))
        current += timedelta(days=1)
    return generated


class CalendarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_events(
        self,
        user_id: int,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[CalendarEvent]:
        window_start, window_end = self._window(start_at, end_at)
        records = await self._list_records(user_id)
        occurrences = self._expand_records(records, window_start, window_end)
        occurrences = await self._apply_exceptions(user_id, occurrences)
        conflicted = self._conflicted_occurrence_keys(occurrences)
        return [self._to_schema(item, item.key in conflicted) for item in occurrences]

    async def list_event_schemas_for_graph(self, user_id: int) -> list[CalendarEvent]:
        start_at, end_at = _default_window()
        return await self.list_events(user_id, start_at=start_at, end_at=end_at)

    async def get_event(self, event_id: int, user_id: int) -> CalendarEventRecord | None:
        statement = select(CalendarEventRecord).where(
            CalendarEventRecord.id == event_id, CalendarEventRecord.user_id == user_id
        )
        return await self._session.scalar(statement)

    async def get_event_schema(self, event_id: int, user_id: int) -> CalendarEvent | None:
        record = await self.get_event(event_id, user_id)
        if record is None:
            return None
        occurrences = self._expand_records([record], record.start_at, record.end_at)
        return self._to_schema(occurrences[0], False)

    async def create_event(
        self, user_id: int, request: CalendarEventCreate
    ) -> CalendarEventRecord:
        record = CalendarEventRecord(
            user_id=user_id,
            title=request.title,
            category=request.category,
            location=request.location,
            notes=request.notes,
            start_at=_storage_datetime(request.start_at),
            end_at=_storage_datetime(request.end_at),
            timezone=request.timezone,
            recurrence_type=request.recurrence.type,
            recurrence_interval=request.recurrence.interval,
            recurrence_days=request.recurrence.days_of_week,
            recurrence_until=_storage_datetime(request.recurrence.until),
            recurrence_count=request.recurrence.count,
            is_all_day=request.is_all_day,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def update_event(
        self, record: CalendarEventRecord, request: CalendarEventUpdate
    ) -> CalendarEventRecord:
        values = request.model_dump(exclude_unset=True)
        values.pop("participant_ids", None)  # 去家庭化：忽略参与者字段
        recurrence = values.pop("recurrence", None)
        if recurrence is not None:
            record.recurrence_type = recurrence["type"]
            record.recurrence_interval = recurrence["interval"]
            record.recurrence_days = recurrence["days_of_week"]
            record.recurrence_until = _storage_datetime(recurrence["until"])
            record.recurrence_count = recurrence["count"]
        for key, value in values.items():
            if key in {"start_at", "end_at"}:
                value = _storage_datetime(value)
            setattr(record, key, value)
        if record.end_at <= record.start_at:
            raise CalendarRepositoryError("end_at must be after start_at")
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def delete_event(self, record: CalendarEventRecord) -> None:
        await self._session.execute(
            delete(CalendarEventRecord).where(CalendarEventRecord.id == record.id)
        )
        await self._session.commit()

    async def create_exception(
        self,
        record: CalendarEventRecord,
        request: CalendarOccurrenceExceptionCreate,
    ) -> CalendarEventException:
        exception = await self._session.scalar(
            select(CalendarEventException).where(
                CalendarEventException.event_id == record.id,
                CalendarEventException.occurrence_start_at
                == _storage_datetime(request.occurrence_start_at),
            )
        )
        if exception is None:
            exception = CalendarEventException(
                user_id=record.user_id,
                event_id=record.id,
                occurrence_start_at=_storage_datetime(request.occurrence_start_at),
            )
            self._session.add(exception)
        exception.action = request.action
        exception.override = request.override
        await self._session.commit()
        await self._session.refresh(exception)
        return exception

    async def list_exceptions(self, event_id: int, user_id: int) -> list[CalendarEventException]:
        return list(
            (
                await self._session.scalars(
                    select(CalendarEventException)
                    .where(
                        CalendarEventException.event_id == event_id,
                        CalendarEventException.user_id == user_id,
                    )
                    .order_by(CalendarEventException.occurrence_start_at)
                )
            ).all()
        )

    async def delete_exception(self, exception_id: int, user_id: int) -> bool:
        record = await self._session.scalar(
            select(CalendarEventException).where(
                CalendarEventException.id == exception_id,
                CalendarEventException.user_id == user_id,
            )
        )
        if record is None:
            return False
        await self._session.delete(record)
        await self._session.commit()
        return True

    async def _apply_exceptions(
        self, user_id: int, occurrences: list[CalendarOccurrence]
    ) -> list[CalendarOccurrence]:
        records = list(
            (
                await self._session.scalars(
                    select(CalendarEventException).where(
                        CalendarEventException.user_id == user_id
                    )
                )
            ).all()
        )
        by_key = {
            (item.event_id, _comparison_datetime(item.occurrence_start_at)): item
            for item in records
        }
        result: list[CalendarOccurrence] = []
        for occurrence in occurrences:
            exception = by_key.get((occurrence.event.id, _comparison_datetime(occurrence.start_at)))
            if exception is None:
                result.append(occurrence)
                continue
            if exception.action == "cancel":
                continue
            start_at = exception.override.get("start_at", occurrence.start_at)
            end_at = exception.override.get("end_at", occurrence.end_at)
            if isinstance(start_at, str):
                start_at = datetime.fromisoformat(start_at)
            if isinstance(end_at, str):
                end_at = datetime.fromisoformat(end_at)
            result.append(
                CalendarOccurrence(
                    occurrence.event,
                    start_at,
                    end_at,
                    occurrence.sequence,
                    exception.override,
                )
            )
        return result

    async def find_conflicts(
        self,
        user_id: int,
        *,
        start_at: datetime,
        end_at: datetime,
        recurrence: CalendarRecurrenceRule,
        exclude_event_id: int | None = None,
    ) -> list[CalendarConflict]:
        window_start, window_end = self._conflict_window(start_at, end_at, recurrence)
        draft_occurrences = _iter_occurrences(
            start_at=start_at,
            end_at=end_at,
            recurrence=recurrence,
            window_start=window_start,
            window_end=window_end,
        )
        records = [
            item
            for item in await self._list_records(user_id)
            if exclude_event_id is None or item.id != exclude_event_id
        ]
        existing = self._expand_records(records, window_start, window_end)
        existing = await self._apply_exceptions(user_id, existing)
        conflicts: list[CalendarConflict] = []
        seen: set[tuple[int, datetime]] = set()
        for draft_start, draft_end in draft_occurrences:
            for occurrence in existing:
                if not _overlaps(draft_start, draft_end, occurrence.start_at, occurrence.end_at):
                    continue
                key = (occurrence.event.id, occurrence.start_at)
                if key in seen:
                    continue
                seen.add(key)
                conflicts.append(self._to_conflict(occurrence))
        return conflicts

    @staticmethod
    def _window(start_at: datetime | None, end_at: datetime | None) -> tuple[datetime, datetime]:
        if start_at is None or end_at is None:
            return _default_window()
        if end_at <= start_at:
            raise CalendarRepositoryError("end_at must be after start_at")
        return start_at, end_at

    @staticmethod
    def _conflict_window(
        start_at: datetime, end_at: datetime, recurrence: CalendarRecurrenceRule
    ) -> tuple[datetime, datetime]:
        if recurrence.type == "none":
            return start_at, end_at
        if recurrence.until is not None:
            return start_at, recurrence.until + (end_at - start_at)
        if recurrence.count is not None:
            return start_at, start_at + timedelta(days=min(730, recurrence.count * 31))
        return start_at, start_at + timedelta(days=180)

    async def _list_records(self, user_id: int) -> list[CalendarEventRecord]:
        statement = (
            select(CalendarEventRecord)
            .where(CalendarEventRecord.user_id == user_id)
            .order_by(CalendarEventRecord.start_at, CalendarEventRecord.id)
        )
        return list((await self._session.scalars(statement)).all())

    @staticmethod
    def _recurrence(record: CalendarEventRecord) -> CalendarRecurrenceRule:
        return CalendarRecurrenceRule(
            type=record.recurrence_type,  # type: ignore[arg-type]
            interval=record.recurrence_interval,
            days_of_week=list(record.recurrence_days or []),
            until=record.recurrence_until,
            count=record.recurrence_count,
        )

    def _expand_records(
        self,
        records: list[CalendarEventRecord],
        window_start: datetime,
        window_end: datetime,
    ) -> list[CalendarOccurrence]:
        occurrences: list[CalendarOccurrence] = []
        for record in records:
            for index, (start_at, end_at) in enumerate(
                _iter_occurrences(
                    start_at=record.start_at,
                    end_at=record.end_at,
                    recurrence=self._recurrence(record),
                    window_start=window_start,
                    window_end=window_end,
                )
            ):
                occurrences.append(CalendarOccurrence(record, start_at, end_at, index))
        return sorted(
            occurrences,
            key=lambda item: (_comparison_datetime(item.start_at), item.event.id),
        )

    @staticmethod
    def _conflicted_occurrence_keys(
        occurrences: list[CalendarOccurrence],
    ) -> set[tuple[int, int]]:
        conflicted: set[tuple[int, int]] = set()
        for index, occurrence in enumerate(occurrences):
            for other in occurrences[index + 1 :]:
                if _overlaps(occurrence.start_at, occurrence.end_at, other.start_at, other.end_at):
                    conflicted.add(occurrence.key)
                    conflicted.add(other.key)
        return conflicted

    def _to_schema(self, occurrence: CalendarOccurrence, conflict: bool) -> CalendarEvent:
        record = occurrence.event
        override = occurrence.override or {}
        return CalendarEvent(
            id=record.id,
            title=str(override.get("title", record.title)),
            member="",
            day=WEEKDAY_LABELS[occurrence.start_at.weekday()],
            time=occurrence.start_at.strftime("%H:%M"),
            category=str(override.get("category", record.category)),
            conflict=conflict,
            start_at=record.start_at,
            end_at=record.end_at,
            timezone=record.timezone,
            location=str(override.get("location", record.location)),
            notes=str(override.get("notes", record.notes)),
            participant_ids=[],
            participants=[],
            recurrence=self._recurrence(record),
            occurrence_start_at=occurrence.start_at,
            occurrence_end_at=occurrence.end_at,
        )

    @staticmethod
    def _to_conflict(occurrence: CalendarOccurrence) -> CalendarConflict:
        override = occurrence.override or {}
        return CalendarConflict(
            event_id=occurrence.event.id,
            title=str(override.get("title", occurrence.event.title)),
            start_at=occurrence.start_at,
            end_at=occurrence.end_at,
            participant_ids=[],
            participants=[],
        )
