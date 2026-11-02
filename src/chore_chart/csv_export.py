from __future__ import annotations

import csv
from pathlib import Path
from typing import TextIO

from chore_chart.domain import Schedule, TimeRange, Weekday


def write_schedule_csv(schedule: Schedule, output: TextIO) -> None:
    writer = csv.writer(output)
    weekdays = list(Weekday)
    writer.writerow(["Chore", *(day.value for day in weekdays)])

    rows: dict[tuple[int | str, int | str, TimeRange | None], dict[Weekday, str]] = {}
    labels: dict[tuple[int | str, int | str, TimeRange | None], str] = {}
    sections: dict[tuple[int | str, int | str, TimeRange | None], str] = {}
    for assignment in schedule.assignments:
        occurrence = assignment.occurrence
        row_key = (
            occurrence.chore_id or occurrence.chore_name,
            occurrence.section_id or occurrence.section_name,
            occurrence.time_range,
        )
        day_assignments = rows.setdefault(row_key, {})
        if occurrence.weekday in day_assignments:
            raise ValueError(
                f"Duplicate CSV cell for {occurrence.chore_name!r} on {occurrence.weekday.value}."
            )
        day_assignments[occurrence.weekday] = assignment.person_name
        labels[row_key] = occurrence.chore_label or occurrence.chore_name
        sections[row_key] = occurrence.section_name

    for row_key in sorted(
        rows,
        key=lambda item: (
            labels[item].casefold(),
            sections[item].casefold(),
            str(item[0]).casefold(),
            item[2].start_minute if item[2] is not None else -1,
        ),
    ):
        assignments = rows[row_key]
        display_section = sections[row_key]
        writer.writerow(
            [
                f"{labels[row_key]} ({display_section})",
                *(assignments.get(day, "") for day in weekdays),
            ]
        )


def export_schedule_csv(schedule: Schedule, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        write_schedule_csv(schedule, output)
    return path