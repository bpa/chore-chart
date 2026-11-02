import csv
from io import StringIO

import pytest

from chore_chart.csv_export import export_schedule_csv, write_schedule_csv
from chore_chart.domain import Assignment, ChoreOccurrence, Schedule, TimeRange, Weekday


def test_csv_has_weekday_columns_chore_rows_and_blank_cells():
    schedule = Schedule(
        (
            Assignment(ChoreOccurrence("Dishes", Weekday.MONDAY, "Evening"), "Alex"),
            Assignment(ChoreOccurrence("Laundry", Weekday.WEDNESDAY, "Morning"), "Sam"),
        )
    )
    output = StringIO(newline="")

    write_schedule_csv(schedule, output)

    rows = list(csv.reader(StringIO(output.getvalue())))
    assert rows[0] == ["Chore", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert rows[1] == ["Dishes (Evening)", "Alex", "", "", "", "", "", ""]
    assert rows[2] == ["Laundry (Morning)", "", "", "Sam", "", "", "", ""]


def test_csv_rows_are_sorted_and_escaped_by_csv_writer():
    schedule = Schedule(
        (
            Assignment(ChoreOccurrence("Wash, dry", Weekday.TUESDAY, "PM"), "Alex, Sr."),
            Assignment(ChoreOccurrence("Dishes", Weekday.MONDAY, "PM"), "Sam"),
        )
    )
    output = StringIO(newline="")

    write_schedule_csv(schedule, output)

    rows = list(csv.reader(StringIO(output.getvalue())))
    assert rows[1][0] == "Dishes (PM)"
    assert rows[2][0] == "Wash, dry (PM)"
    assert rows[2][2] == "Alex, Sr."


def test_csv_rejects_two_assignments_for_one_matrix_cell():
    schedule = Schedule(
        (
            Assignment(ChoreOccurrence("Dishes", Weekday.MONDAY, "Evening"), "Alex"),
            Assignment(ChoreOccurrence("Dishes", Weekday.MONDAY, "Evening"), "Sam"),
        )
    )

    with pytest.raises(ValueError, match="Duplicate CSV cell"):
        write_schedule_csv(schedule, StringIO())


def test_csv_keeps_same_chore_and_group_ranges_in_separate_rows_without_printing_ranges():
    schedule = Schedule(
        (
            Assignment(
                ChoreOccurrence(
                    "Dinner prep",
                    Weekday.MONDAY,
                    "Dinner",
                    time_range=TimeRange.parse("16:00-17:00"),
                ),
                "Alex",
            ),
            Assignment(
                ChoreOccurrence(
                    "Dinner prep",
                    Weekday.TUESDAY,
                    "Dinner",
                    time_range=TimeRange.parse("18:00-19:00"),
                ),
                "Alex",
            ),
        )
    )
    output = StringIO(newline="")

    write_schedule_csv(schedule, output)

    rows = list(csv.reader(StringIO(output.getvalue())))
    assert [row[0] for row in rows[1:]] == ["Dinner prep (Dinner)", "Dinner prep (Dinner)"]


def test_export_creates_parent_folder_and_writes_utf8_bom(tmp_path):
    schedule = Schedule((Assignment(ChoreOccurrence("Dishes", Weekday.MONDAY, "Evening"), "Alex"),))
    destination = tmp_path / "exports" / "week.csv"

    result = export_schedule_csv(schedule, destination)

    assert result == destination
    assert destination.read_bytes().startswith(b"\xef\xbb\xbf")
    assert "Dishes (Evening)" in destination.read_text(encoding="utf-8-sig")