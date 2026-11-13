"""Data for the drag-on-timeline booking picker (spec §6.7).

One day for one equipment type: 15-minute slots spanning the earliest opening to the latest closing time
of the labs holding its units; a summary of free units per slot; and one row per unit with merged
segments (booked, yours, maintenance, closed). Students see other people's bookings as "Booked" only.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import EquipmentType, EquipmentUnit, Role, User
from ..rules import SLOT_MINUTES
from ..timeutil import utcnow
from .availability import busy_by_unit, in_service_units, local_day_bounds, type_timezone
from .calendar import LabDayCache


@dataclass
class Segment:
    start: int  # slot index
    span: int
    kind: str  # booked | own | editing | maintenance | closed
    label: str
    title: str


@dataclass
class UnitRow:
    unit: EquipmentUnit
    segments: list[Segment]


@dataclass
class SlotSummary:
    free: int
    total: int

    @property
    def level(self) -> str:
        """Shading bucket: all free, some free, none free (spec §6.7 summary row)."""
        if self.total == 0 or self.free == 0:
            return "none"
        return "all" if self.free == self.total else ("most" if self.free * 2 >= self.total else "few")


@dataclass
class Timeline:
    day: date
    tz: ZoneInfo
    slots: list[datetime] = field(default_factory=list)
    summary: list[SlotSummary] = field(default_factory=list)
    rows: list[UnitRow] = field(default_factory=list)
    closed_message: str | None = None
    now_position: float | None = None  # fraction of the track, when "now" is inside the range

    @property
    def start_minute(self) -> int:
        first = self.slots[0]
        return first.hour * 60 + first.minute

    @property
    def hour_marks(self) -> list[tuple[int, str]]:
        return [(i, f"{s:%H}:00") for i, s in enumerate(self.slots) if s.minute == 0]

    def slot_label(self, index: int) -> str:
        return f"{self.slots[index]:%H:%M}" if index < len(self.slots) else f"{self.end:%H:%M}"

    @property
    def end(self) -> datetime:
        return self.slots[-1] + timedelta(minutes=SLOT_MINUTES)


def _merge(states: list[tuple[str, str, str] | None]) -> list[Segment]:
    segments: list[Segment] = []
    for i, state in enumerate(states):
        if state is None:
            continue
        kind, label, title = state
        last = segments[-1] if segments else None
        if last and last.start + last.span == i and (last.kind, last.label, last.title) == state:
            last.span += 1
        else:
            segments.append(Segment(i, 1, kind, label, title))
    return segments


def build(eq_type: EquipmentType, day: date, viewer: User, editing_booking_id: int | None = None,
          now: datetime | None = None) -> Timeline:
    now = now or utcnow()
    tz = type_timezone(eq_type)
    timeline = Timeline(day=day, tz=tz)
    units = in_service_units(eq_type)
    if not units:
        timeline.closed_message = "No units are in service."
        return timeline

    cache = LabDayCache()
    lab_days = {u.lab.id: cache.get(u.lab, day) for u in units}
    intervals = [iv for d in lab_days.values() for iv in d.intervals]
    if not intervals:
        reasons = [d.holiday for d in lab_days.values() if d.holiday]
        timeline.closed_message = f"Closed — {reasons[0]}" if reasons else "The lab is closed this day."
        return timeline

    start = min(a for a, _ in intervals).astimezone(tz)
    end = max(b for _, b in intervals).astimezone(tz)
    step = timedelta(minutes=SLOT_MINUTES)
    t = start
    while t < end:
        timeline.slots.append(t)
        t += step

    busy = busy_by_unit([u.id for u in units], *local_day_bounds(day, tz))
    show_names = viewer.has_role(Role.TEACHER, Role.LAB_TECH)
    free_counts = [0] * len(timeline.slots)
    for unit in units:
        lab_day = lab_days[unit.lab.id]
        states: list[tuple[str, str, str] | None] = []
        for i, slot in enumerate(timeline.slots):
            slot_end = slot + step
            state = None
            if not lab_day.contains(slot, slot_end):
                state = ("closed", "Closed", f"{unit.lab.name} is closed")
            for item in busy.get(unit.id, []):
                if item.starts_at < slot_end and slot < item.ends_at:
                    if item.block is not None:
                        state = ("maintenance", "Maintenance", item.block.reason)
                    elif item.booking.id == editing_booking_id:
                        state = None  # the booking being edited doesn't block its own new time
                    elif item.booking.user_id == viewer.id:
                        state = ("own", "Your booking", f"Your booking #{item.booking.id}")
                    else:
                        who = item.booking.user.name if show_names and item.booking.user else "Booked"
                        state = ("booked", "Booked", who)
                    break
            if state is None:
                free_counts[i] += 1
            states.append(state)
        timeline.rows.append(UnitRow(unit, _merge(states)))

    timeline.summary = [SlotSummary(free, len(units)) for free in free_counts]
    if start <= now < end:
        timeline.now_position = (now - start) / (end - start)
    return timeline
