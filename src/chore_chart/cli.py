from __future__ import annotations

import argparse
import csv
import os
import shlex
import subprocess
import sys
import tempfile
from typing import Sequence

from chore_chart.csv_export import export_schedule_csv
from chore_chart.domain import AvailabilitySlot, ChoreOccurrence, Schedule, TimeRange, Weekday
from chore_chart.storage import SQLiteRepository


_DAY_ALIASES = {
    "m": Weekday.MONDAY,
    "tu": Weekday.TUESDAY,
    "t": Weekday.TUESDAY,
    "w": Weekday.WEDNESDAY,
    "th": Weekday.THURSDAY,
    "f": Weekday.FRIDAY,
    "sa": Weekday.SATURDAY,
    "s": Weekday.SATURDAY,
    "su": Weekday.SUNDAY,
}
_AVAILABILITY_COLUMNS = ("M", "Tu", "W", "Th", "F", "Sa", "Su")


def _parse_weekday(value: str) -> Weekday:
    token = value.strip().casefold()
    return _DAY_ALIASES[token] if token in _DAY_ALIASES else Weekday.parse(value)


def _parse_days(value: str) -> tuple[Weekday, ...]:
    endpoints = value.strip().split("-")
    if len(endpoints) == 1:
        return (_parse_weekday(endpoints[0]),)
    if len(endpoints) != 2:
        raise ValueError("Use one day or an inclusive range such as m-f or m-s.")

    start = _parse_weekday(endpoints[0])
    end = _parse_weekday(endpoints[1])
    weekdays = list(Weekday)
    start_index = start.order
    end_index = end.order
    if end_index < start_index:
        return tuple(weekdays[start_index:] + weekdays[: end_index + 1])
    return tuple(weekdays[start_index : end_index + 1])


def _parse_availability_slot(value: str) -> tuple[tuple[Weekday, ...], TimeRange]:
    weekday_text, separator, range_text = value.partition(":")
    if not separator or not range_text.strip():
        raise argparse.ArgumentTypeError("Use DAY:START-END, for example m-f:16:00-20:00.")
    try:
        return _parse_days(weekday_text), TimeRange.parse(range_text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _parse_chore_slot(value: str) -> tuple[tuple[Weekday, ...], str, TimeRange | None]:
    weekday_text, separator, chore_time = value.partition(":")
    if not separator or not chore_time.strip():
        raise argparse.ArgumentTypeError("Use DAY:GROUP[@START-END], for example m:Evening@17:00-18:30.")
    time_label, range_separator, range_text = chore_time.partition("@")
    if not time_label.strip() or range_separator and not range_text.strip():
        raise argparse.ArgumentTypeError("Use DAY:GROUP[@START-END], for example m:Evening@17:00-18:30.")
    try:
        time_range = TimeRange.parse(range_text) if range_separator else None
        return _parse_days(weekday_text), time_label.strip(), time_range
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _availability_editor() -> list[str]:
    configured_editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if configured_editor:
        command = shlex.split(configured_editor, posix=os.name != "nt")
        if os.name == "nt" and command:
            command[0] = command[0].strip('\"')
    else:
        command = ["notepad.exe"] if os.name == "nt" else ["vi"]
    if not command:
        raise ValueError("No editor command is configured.")
    if os.path.basename(command[0]).casefold() in {
        "code", "code.cmd", "code.exe", "code-insiders", "code-insiders.cmd", "code-insiders.exe"
    } and "--wait" not in command:
        command.append("--wait")
    return command


def _availability_table(time_ranges: Sequence[TimeRange], availability: frozenset[AvailabilitySlot]) -> str:
    headers = ("Time", *_AVAILABILITY_COLUMNS)
    rows = [headers]
    for time_range in time_ranges:
        row = [str(time_range)]
        row.extend(
            "x" if AvailabilitySlot(day, time_range) in availability else ""
            for day in Weekday
        )
        rows.append(tuple(row))

    widths = [max(len(row[index]) for row in rows) for index in range(len(headers))]

    def format_row(row: Sequence[str]) -> str:
        cells = []
        for cell, width in zip(row, widths):
            rendered = f'"{cell.replace(chr(34), chr(34) * 2)}"' if "|" in cell or '\"' in cell else cell
            cells.append(rendered.ljust(width))
        return " | ".join(cells).rstrip()

    return "# Mark available cells with x. Add or remove time-range rows as needed.\n" + "\n".join(
        format_row(row) for row in rows
    ) + "\n"


def _parse_availability_table(contents: str) -> set[AvailabilitySlot]:
    lines = [line for line in contents.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError("Availability table is empty; keep the header row.")
    try:
        header = next(csv.reader([lines[0]], delimiter="|", skipinitialspace=True, strict=True))
    except csv.Error as error:
        raise ValueError("Could not read the availability table header.") from error
    expected_header = ("Time", *_AVAILABILITY_COLUMNS)
    if tuple(cell.strip().casefold() for cell in header) != tuple(
        cell.casefold() for cell in expected_header
    ):
        raise ValueError("Availability table header must be: Time | M | Tu | W | Th | F | Sa | Su.")

    slots: set[AvailabilitySlot] = set()
    seen_ranges: set[TimeRange] = set()
    for line_number, line in enumerate(lines[1:], start=2):
        try:
            cells = next(csv.reader([line], delimiter="|", skipinitialspace=True, strict=True))
        except csv.Error as error:
            raise ValueError(f"Could not read availability table row {line_number}.") from error
        cells = [cell.strip() for cell in cells]
        if len(cells) != len(expected_header):
            raise ValueError(f"Availability table row {line_number} must have 8 columns.")
        if not cells[0]:
            raise ValueError(f"Availability table row {line_number} needs a time range.")
        try:
            time_range = TimeRange.parse(cells[0])
        except ValueError as error:
            raise ValueError(f"Invalid time range in availability table row {line_number}: {error}") from error
        if time_range in seen_ranges:
            raise ValueError(f"Availability table has duplicate time range {time_range}.")
        seen_ranges.add(time_range)
        for day, marker in zip(Weekday, cells[1:]):
            if marker.casefold() not in {"", "x"}:
                raise ValueError(f"Use x or a blank cell in availability table row {line_number}.")
            if marker.casefold() == "x":
                slots.add(AvailabilitySlot(day, time_range))
    return slots


def _edit_availability(person_name: str, repository: SQLiteRepository) -> None:
    normalized_name = person_name.strip()
    person = next(
        (item for item in repository.list_people() if item.name == normalized_name),
        None,
    )
    if person is None:
        raise ValueError(f"Unknown person {person_name!r}.")

    time_ranges = sorted(
        {slot.time_range for slot in person.availability},
        key=lambda item: (item.start_minute, item.end_minute),
    )

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt", delete=False) as temporary:
            temporary_path = temporary.name
            temporary.write(_availability_table(time_ranges, person.availability))
        result = subprocess.run(_availability_editor() + [temporary_path], check=False)
        if result.returncode:
            raise ValueError(f"Editor exited with status {result.returncode}.")
        with open(temporary_path, encoding="utf-8") as edited_file:
            slots = _parse_availability_table(edited_file.read())
        repository.set_availability(person.name, slots)
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _set_all_availability(person_name: str, repository: SQLiteRepository) -> None:
    people = repository.list_people()
    normalized_name = person_name.strip()
    person = next((item for item in people if item.name == normalized_name), None)
    if person is None:
        raise ValueError(f"Unknown person {person_name!r}.")

    slots = {
        AvailabilitySlot(day, TimeRange(0, 1440)) for day in Weekday
    }
    repository.set_availability(person.name, slots)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chore-chart",
        description="Manage household chores and generate fair weekly charts.",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("CHORE_CHART_DB", "chore_chart.sqlite3"),
        help="SQLite database path (default: CHORE_CHART_DB or ./chore_chart.sqlite3).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    person = commands.add_parser("person", help="Manage household members.")
    person_commands = person.add_subparsers(dest="person_command", required=True)
    add_person = person_commands.add_parser("add", help="Add a person.")
    add_person.add_argument("name", nargs="+")
    person_commands.add_parser("list", help="List people and availability.")

    availability = commands.add_parser("availability", help="Manage availability.")
    availability_commands = availability.add_subparsers(dest="availability_command", required=True)
    set_availability = availability_commands.add_parser("set", help="Replace a person's available slots.")
    set_availability.add_argument("person")
    set_availability.add_argument("--slot", action="append", type=_parse_availability_slot, required=True)
    clear_availability = availability_commands.add_parser("clear", help="Clear a person's available slots.")
    clear_availability.add_argument("person")
    edit_availability = availability_commands.add_parser("edit", help="Edit a person's availability in a text editor.")
    edit_availability.add_argument("person")
    all_availability = availability_commands.add_parser(
        "all",
        help="Set one person's availability to every known time label on every weekday.",
    )
    all_availability.add_argument("person")

    chore = commands.add_parser("chore", help="Manage chores.")
    chore_commands = chore.add_subparsers(dest="chore_command", required=True)
    add_chore = chore_commands.add_parser("add", help="Add a chore and its weekly occurrences.")
    add_chore.add_argument("name", nargs="+")
    add_chore.add_argument("--label", help="Display label used in chore lists and CSV.")
    add_chore.add_argument("-o", "--occurrence", action="append", type=_parse_chore_slot, required=True)
    chore_commands.add_parser("list", help="List chores and their occurrences.")
    remove_chore = chore_commands.add_parser(
        "remove",
        aliases=["del", "rm"],
        help="Remove a chore or selected weekday/time occurrences.",
    )
    remove_chore.add_argument("name", nargs="+")
    remove_chore.add_argument("-o", "--occurrence", action="append", type=_parse_chore_slot)

    rule = commands.add_parser("rule", help="Manage weekly assignment-count rules.")
    rule_commands = rule.add_subparsers(dest="rule_command", required=True)
    set_rule = rule_commands.add_parser("set", help="Set a person's weekly minimum and maximum.")
    set_rule.add_argument("person")
    set_rule.add_argument("--min", type=int, default=0, dest="minimum")
    set_rule.add_argument("--max", type=int, dest="maximum")
    rule_commands.add_parser("list", help="List assignment-count rules.")

    schedule = commands.add_parser("schedule", help="Generate, inspect, or export schedules.")
    schedule_commands = schedule.add_subparsers(dest="schedule_command", required=True)
    generate = schedule_commands.add_parser("generate", help="Generate and save a schedule as CSV.")
    generate.add_argument("--seed", type=int)
    generate.add_argument("--output", default="chore-chart.csv")
    schedule_commands.add_parser("list", help="List saved schedules.")
    export = schedule_commands.add_parser("export", help="Export a saved schedule as CSV.")
    export.add_argument("schedule_id", type=int)
    export.add_argument("--output", required=True)
    return parser


def _print_schedule(schedule: Schedule) -> None:
    for assignment in schedule.assignments:
        occurrence = assignment.occurrence
        print(
            f"{occurrence.weekday.value:9}  {occurrence.time_label:16}  "
            f"{str(occurrence.time_range) if occurrence.time_range else '':11}  "
            f"{(occurrence.chore_label or occurrence.chore_name):24}  {assignment.person_name}"
        )


def _run(args: argparse.Namespace, repository: SQLiteRepository) -> int:
    if args.command == "person":
        if args.person_command == "add":
            person = repository.add_person(" ".join(args.name))
            print(f"Added {person.name}.")
        else:
            for person in repository.list_people():
                slots = sorted(
                    person.availability,
                    key=lambda slot: (
                        slot.weekday.order,
                        slot.time_range.start_minute,
                        slot.time_range.end_minute,
                    ),
                )
                formatted = ", ".join(f"{slot.weekday.value}:{slot.time_range}" for slot in slots)
                print(f"{person.name}: {formatted or 'no availability set'}")
        return 0

    if args.command == "availability":
        if args.availability_command == "all":
            _set_all_availability(args.person, repository)
            print(f"Set all availability for {args.person}.")
            return 0
        if args.availability_command == "edit":
            _edit_availability(args.person, repository)
            print(f"Updated availability for {args.person}.")
            return 0
        slots = [
            AvailabilitySlot(day, time_range)
            for days, time_range in args.slot
            for day in days
        ] if args.availability_command == "set" else []
        repository.set_availability(args.person, slots)
        print(f"Updated availability for {args.person}.")
        return 0

    if args.command == "chore":
        if args.chore_command == "add":
            name = " ".join(args.name)
            occurrences = tuple(
                ChoreOccurrence(name, weekday, time_label, time_range=time_range)
                for days, time_label, time_range in args.occurrence
                for weekday in days
            )
            chore = repository.add_chore(name, occurrences, label=args.label)
            print(f"Added {name} with {len(chore.occurrences)} active occurrence(s).")
        elif args.chore_command in {"remove", "del", "rm"}:
            name = " ".join(args.name)
            occurrences = None
            if args.occurrence:
                occurrences = tuple(
                    ChoreOccurrence(name, weekday, time_label, time_range=time_range)
                    for days, time_label, time_range in args.occurrence
                    for weekday in days
                )
            removed = repository.remove_chore(name, occurrences)
            if occurrences is None:
                print(f"Removed chore {name}.")
            else:
                print(f"Removed {removed} occurrence(s) from {name}.")
        else:
            for chore in repository.list_chores():
                occurrences = ", ".join(
                    f"{item.weekday.value}:{item.time_label}"
                    + (f"@{item.time_range}" if item.time_range else "")
                    for item in chore.occurrences
                )
                name = chore.name if chore.label == chore.name else f"{chore.label} [{chore.name}]"
                print(f"{name}: {occurrences}")
        return 0

    if args.command == "rule":
        if args.rule_command == "set":
            rule = repository.set_rule(args.person, args.minimum, args.maximum)
            maximum = "unlimited" if rule.maximum is None else str(rule.maximum)
            print(f"{rule.person_name}: minimum {rule.minimum}, maximum {maximum}.")
        else:
            for rule in repository.list_rules():
                maximum = "unlimited" if rule.maximum is None else str(rule.maximum)
                print(f"{rule.person_name}: minimum {rule.minimum}, maximum {maximum}")
        return 0

    if args.command == "schedule":
        if args.schedule_command == "generate":
            schedule = repository.generate_and_save(seed=args.seed)
            output = export_schedule_csv(schedule, args.output)
            _print_schedule(schedule)
            print(f"Saved schedule {schedule.schedule_id} to {output}.")
        elif args.schedule_command == "list":
            for schedule in repository.list_schedules():
                created_at = schedule.created_at.isoformat() if schedule.created_at else "unknown time"
                print(f"{schedule.schedule_id}: {created_at} ({len(schedule.assignments)} assignments)")
        else:
            schedule = repository.get_schedule(args.schedule_id)
            output = export_schedule_csv(schedule, args.output)
            print(f"Exported schedule {schedule.schedule_id} to {output}.")
        return 0

    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repository = SQLiteRepository(args.database)
    try:
        return _run(args, repository)
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())