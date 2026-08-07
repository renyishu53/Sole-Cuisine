import re
from datetime import datetime, time, timedelta

from app.schemas import CalendarEvent, MemberProfile
from app.schemas.domain import (
    CalendarAgentConflict,
    CalendarAgentResult,
    CalendarAlternativeSlot,
)


def _plain(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _bounds(event: CalendarEvent) -> tuple[datetime, datetime] | None:
    start_at = event.occurrence_start_at or event.start_at
    end_at = event.occurrence_end_at or event.end_at
    if start_at is None or end_at is None:
        return None
    return _plain(start_at), _plain(end_at)


def _overlaps(
    start_at: datetime,
    end_at: datetime,
    other_start: datetime,
    other_end: datetime,
) -> bool:
    return start_at < other_end and end_at > other_start


def _availability_allows(
    member: MemberProfile | None, start_at: datetime, end_at: datetime
) -> bool:
    if member is None:
        return True
    availability = member.availability.strip()
    if not availability or availability == "待补充":
        return True
    if "仅周末" in availability and start_at.weekday() < 5:
        return False

    match = re.search(r"(\d{1,2}):(\d{2})\s*后", availability)
    if match and ("工作日" not in availability or start_at.weekday() < 5):
        earliest = time(int(match.group(1)), int(match.group(2)))
        if start_at.time() < earliest:
            return False
    match = re.search(r"(\d{1,2}):(\d{2})\s*前", availability)
    if match and ("工作日" not in availability or start_at.weekday() < 5):
        latest = time(int(match.group(1)), int(match.group(2)))
        if end_at.time() > latest:
            return False
    return True


def _round_up_half_hour(value: datetime) -> datetime:
    value = value.replace(second=0, microsecond=0)
    if value.minute == 0 or value.minute == 30:
        return value
    minutes = 30 - value.minute % 30
    return value + timedelta(minutes=minutes)


def _find_alternatives(
    *,
    anchor: datetime,
    duration: timedelta,
    participant_ids: list[int],
    events: list[CalendarEvent],
    member_by_id: dict[int, MemberProfile],
    limit: int,
) -> list[CalendarAlternativeSlot]:
    event_bounds = [(event, _bounds(event)) for event in events]
    slots: list[CalendarAlternativeSlot] = []
    first_candidate = _round_up_half_hour(anchor)
    for day_offset in range(8):
        day = (first_candidate + timedelta(days=day_offset)).date()
        candidate = datetime.combine(day, time(8))
        if day_offset == 0:
            candidate = max(candidate, first_candidate)
        latest_start = datetime.combine(day, time(21)) - duration
        while candidate <= latest_start:
            candidate_end = candidate + duration
            if all(
                _availability_allows(member_by_id.get(member_id), candidate, candidate_end)
                for member_id in participant_ids
            ):
                occupied = any(
                    bounds is not None
                    and set(participant_ids) & set(event.participant_ids)
                    and _overlaps(candidate, candidate_end, bounds[0], bounds[1])
                    for event, bounds in event_bounds
                )
                if not occupied:
                    slots.append(
                        CalendarAlternativeSlot(
                            start_at=candidate,
                            end_at=candidate_end,
                            participant_ids=participant_ids,
                            label=f"{candidate:%m-%d %H:%M}-{candidate_end:%H:%M}",
                        )
                    )
                    if len(slots) >= limit:
                        return slots
            candidate += timedelta(minutes=30)
    return slots


def analyze_calendar(
    events: list[CalendarEvent],
    members: list[MemberProfile],
    *,
    alternative_limit: int = 3,
) -> CalendarAgentResult:
    """Detect real overlapping occurrences and suggest open replacement slots."""
    usable = [(event, _bounds(event)) for event in events]
    usable = [(event, bounds) for event, bounds in usable if bounds is not None]
    member_by_id = {member.id: member for member in members}
    conflicts: list[CalendarAgentConflict] = []
    affected_member_ids: set[int] = set()

    for index, (event, bounds) in enumerate(usable):
        assert bounds is not None
        for other, other_bounds in usable[index + 1 :]:
            assert other_bounds is not None
            shared = sorted(set(event.participant_ids) & set(other.participant_ids))
            if not shared or not _overlaps(bounds[0], bounds[1], other_bounds[0], other_bounds[1]):
                continue
            overlap_start = max(bounds[0], other_bounds[0])
            overlap_end = min(bounds[1], other_bounds[1])
            participant_names = [
                member_by_id[member_id].name for member_id in shared if member_id in member_by_id
            ]
            names = "、".join(participant_names) or "共同参与成员"
            conflicts.append(
                CalendarAgentConflict(
                    event_ids=[event.id, other.id],
                    titles=[event.title, other.title],
                    start_at=overlap_start,
                    end_at=overlap_end,
                    participant_ids=shared,
                    participants=participant_names,
                    message=(
                        f"{names} 的“{event.title}”与“{other.title}”在 "
                        f"{overlap_start:%m-%d %H:%M}-{overlap_end:%H:%M} 冲突"
                    ),
                )
            )
            affected_member_ids.update(shared)

    alternatives: list[CalendarAlternativeSlot] = []
    if conflicts:
        first = conflicts[0]
        source_events = [event for event, _ in usable if event.id in first.event_ids]
        source_bounds = [
            bounds for event in source_events if (bounds := _bounds(event)) is not None
        ]
        duration = max(
            (end_at - start_at for start_at, end_at in source_bounds),
            default=timedelta(hours=1),
        )
        alternatives = _find_alternatives(
            anchor=first.end_at,
            duration=duration,
            participant_ids=first.participant_ids,
            events=events,
            member_by_id=member_by_id,
            limit=alternative_limit,
        )

    return CalendarAgentResult(
        status="conflict" if conflicts else "clear",
        has_conflict=bool(conflicts),
        checked_event_count=len(usable),
        affected_member_ids=sorted(affected_member_ids),
        conflicts=conflicts,
        alternative_slots=alternatives,
    )
