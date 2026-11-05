from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum


def normalize_chore_text(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


class Weekday(str, Enum):
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"

    @classmethod
    def parse(cls, value: str) -> Weekday:
        normalized = value.strip().casefold()
        for day in cls:
            if day.value.casefold() == normalized or day.name.casefold() == normalized:
                return day
        alias = _WEEKDAY_ALIASES.get(normalized)
        if alias is not None:
            return alias
        choices = ", ".join(day.value for day in cls)
        raise ValueError(f"Unknown weekday {value!r}. Choose one of: {choices}.")

    @property
    def order(self) -> int:
        return list(Weekday).index(self)


_WEEKDAY_ALIASES = {
    "m": Weekday.MONDAY,
    "mo": Weekday.MONDAY,
    "mon": Weekday.MONDAY,
    "t": Weekday.TUESDAY,
    "tu": Weekday.TUESDAY,
    "tur": Weekday.TUESDAY,
    "w": Weekday.WEDNESDAY,
    "we": Weekday.WEDNESDAY,
    "wed": Weekday.WEDNESDAY,
    "th": Weekday.THURSDAY,
    "thu": Weekday.THURSDAY,
    "f": Weekday.FRIDAY,
    "fr": Weekday.FRIDAY,
    "fri": Weekday.FRIDAY,
    "s": Weekday.SATURDAY,
    "sa": Weekday.SATURDAY,
    "sat": Weekday.SATURDAY,
    "su": Weekday.SUNDAY,
    "sun": Weekday.SUNDAY,
}


@dataclass(frozen=True, slots=True)
class TimeRange:
    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute < self.end_minute <= 1440:
            raise ValueError("Time range must start before it ends within the same day.")

    @classmethod
    def parse(cls, value: str) -> TimeRange:
        match = re.fullmatch(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})", value.strip())
        if match is None:
            raise ValueError("Use a 24-hour time range such as 16:00-17:30.")
        start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
        if start_hour > 23 or end_hour > 24 or start_minute > 59 or end_minute > 59:
            raise ValueError("Time values must be between 00:00 and 24:00.")
        if end_hour == 24 and end_minute != 0:
            raise ValueError("24:00 is the only valid time after 23:59.")
        return cls(start_hour * 60 + start_minute, end_hour * 60 + end_minute)

    def overlap_minutes(self, other: TimeRange) -> int:
        return max(0, min(self.end_minute, other.end_minute) - max(self.start_minute, other.start_minute))

    def __str__(self) -> str:
        start = f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}"
        end = f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}"
        return f"{start}-{end}"


@dataclass(frozen=True, slots=True)
class BusyBlock:
    weekday: Weekday
    time_range: TimeRange
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.time_range, TimeRange):
            raise ValueError("Busy time must use a valid time range.")
        label = self.label.strip() if self.label is not None else ""
        if len(label) > 160:
            raise ValueError("Busy time labels cannot exceed 160 characters.")
        object.__setattr__(self, "label", label or None)


@dataclass(frozen=True, slots=True)
class Section:
    name: str
    section_id: int | None = None
    order_index: int = 0

    def __post_init__(self) -> None:
        name = normalize_chore_text(self.name)
        if not name:
            raise ValueError("Section name cannot be empty.")
        if self.order_index < 0:
            raise ValueError("Section order cannot be negative.")
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class ChoreOccurrence:
    chore_name: str
    weekday: Weekday
    section_name: str
    occurrence_id: int | None = None
    chore_id: int | None = None
    chore_label: str | None = None
    time_range: TimeRange | None = None
    section_id: int | None = None
    section_order: int = 0
    chore_order: int = 0

    def __post_init__(self) -> None:
        chore_name = normalize_chore_text(self.chore_name)
        section_name = normalize_chore_text(self.section_name)
        if not chore_name:
            raise ValueError("Chore name cannot be empty.")
        if not section_name:
            raise ValueError("Section name cannot be empty.")
        if self.section_order < 0 or self.chore_order < 0:
            raise ValueError("Section and chore order cannot be negative.")
        if self.time_range is not None and not isinstance(self.time_range, TimeRange):
            raise ValueError("Chore occurrence must use a valid time range.")
        object.__setattr__(self, "chore_name", chore_name)
        object.__setattr__(self, "section_name", section_name)
        label = normalize_chore_text(self.chore_label) if self.chore_label is not None else chore_name
        if not label:
            raise ValueError("Chore label cannot be empty.")
        object.__setattr__(self, "chore_label", label)


@dataclass(frozen=True, slots=True)
class Person:
    name: str
    busy_times: frozenset[BusyBlock] = field(default_factory=frozenset)
    person_id: int | None = None
    cell_color: str = "#e7f1ec"
    cell_color_mid: str | None = None
    cell_color_end: str = "#dce8f1"
    cell_fill: str = "solid"
    cell_text_color: str = "#202c29"

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("Person name cannot be empty.")
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class Chore:
    name: str
    occurrences: tuple[ChoreOccurrence, ...]
    chore_id: int | None = None
    label: str | None = None
    section_id: int | None = None
    section_name: str | None = None
    section_order: int = 0
    order_index: int = 0

    def __post_init__(self) -> None:
        name = normalize_chore_text(self.name)
        if not name:
            raise ValueError("Chore name cannot be empty.")
        if not self.occurrences:
            raise ValueError("A chore must have at least one scheduled occurrence.")
        if any(item.chore_name.casefold() != name.casefold() for item in self.occurrences):
            raise ValueError("Each occurrence must belong to its chore.")
        label = normalize_chore_text(self.label) if self.label is not None else name
        if not label:
            raise ValueError("Chore label cannot be empty.")
        section_name = (
            normalize_chore_text(self.section_name)
            if self.section_name is not None
            else self.occurrences[0].section_name
        )
        if any(item.section_name.casefold() != section_name.casefold() for item in self.occurrences):
            raise ValueError("Each chore occurrence must belong to its chore's section.")
        if self.section_order < 0 or self.order_index < 0:
            raise ValueError("Section and chore order cannot be negative.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "section_name", section_name)
        object.__setattr__(
            self,
            "occurrences",
            tuple(
                replace(
                    item,
                    chore_label=label,
                    section_id=self.section_id,
                    section_name=section_name,
                    section_order=self.section_order,
                    chore_order=self.order_index,
                )
                for item in self.occurrences
            ),
        )


@dataclass(frozen=True, slots=True)
class AssignmentRule:
    person_name: str
    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        name = self.person_name.strip()
        if not name:
            raise ValueError("Rule must name a person.")
        if self.minimum < 0:
            raise ValueError("Minimum chore count cannot be negative.")
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("Maximum chore count cannot be less than the minimum.")
        object.__setattr__(self, "person_name", name)


@dataclass(frozen=True, slots=True)
class ChoreAssignmentRule:
    person_name: str
    chore_name: str
    weekday: Weekday
    is_assigned: bool
    rule_id: int | None = None

    def __post_init__(self) -> None:
        person_name = self.person_name.strip()
        chore_name = normalize_chore_text(self.chore_name)
        if not person_name:
            raise ValueError("Rule must name a person.")
        if not chore_name:
            raise ValueError("Rule must name a chore.")
        if not isinstance(self.weekday, Weekday):
            raise ValueError("Rule must use a valid weekday.")
        object.__setattr__(self, "person_name", person_name)
        object.__setattr__(self, "chore_name", chore_name)


@dataclass(frozen=True, slots=True)
class Assignment:
    occurrence: ChoreOccurrence
    person_name: str
    person_id: int | None = None
    assignment_id: int | None = None


@dataclass(frozen=True, slots=True)
class Schedule:
    assignments: tuple[Assignment, ...]
    schedule_id: int | None = None
    seed: int | None = None
    created_at: datetime | None = None