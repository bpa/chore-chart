import csv
import subprocess
import sys
from pathlib import Path

from chore_chart import cli as cli_module
from chore_chart.cli import main


def invoke(database, *arguments):
    return main(["--database", str(database), *arguments])


def test_cli_manages_household_and_exports_saved_schedule(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"
    output = tmp_path / "exports" / "week.csv"

    assert invoke(database, "person", "add", "Alex", "Smith") == 0
    assert invoke(
        database,
        "availability",
        "set",
        "Alex Smith",
        "--slot",
        "Monday:16:00-20:00",
    ) == 0
    assert invoke(
        database,
        "chore",
        "add",
        "Dishes",
        "--occurrence",
        "Monday:Morning@17:00-18:30",
    ) == 0
    assert invoke(database, "rule", "set", "Alex Smith", "--min", "1", "--max", "1") == 0

    assert invoke(database, "person", "list") == 0
    assert "Alex Smith: Monday:16:00-20:00" in capsys.readouterr().out
    assert invoke(database, "chore", "list") == 0
    assert "Dishes: Monday:Morning@17:00-18:30" in capsys.readouterr().out
    assert invoke(database, "rule", "list") == 0
    assert "Alex Smith: minimum 1, maximum 1" in capsys.readouterr().out

    assert invoke(
        database,
        "schedule",
        "generate",
        "--seed",
        "12",
        "--output",
        str(output),
    ) == 0
    generated_output = capsys.readouterr().out
    assert "Saved schedule 1" in generated_output
    with output.open(encoding="utf-8-sig", newline="") as csv_file:
        assert list(csv.reader(csv_file))[1] == [
            "Dishes (Morning 17:00-18:30)",
            "Alex Smith",
            "",
            "",
            "",
            "",
            "",
            "",
        ]

    assert invoke(database, "schedule", "list") == 0
    assert "1:" in capsys.readouterr().out
    exported = tmp_path / "exports" / "saved.csv"
    assert invoke(database, "schedule", "export", "1", "--output", str(exported)) == 0
    assert exported.read_bytes() == output.read_bytes()

    assert invoke(database, "availability", "clear", "Alex Smith") == 0
    assert invoke(database, "person", "list") == 0
    assert "no availability set" in capsys.readouterr().out


def test_cli_reports_record_validation_errors(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"

    assert invoke(database, "person", "add", "Alex") == 0
    assert invoke(database, "person", "add", "Alex") == 2
    assert "already exists" in capsys.readouterr().err


def test_module_entry_point_shows_help():
    result = subprocess.run(
        [sys.executable, "-m", "chore_chart", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "availability" in result.stdout
    assert "schedule" in result.stdout


def test_chore_short_option_expands_weekday_ranges(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"

    assert invoke(
        database,
        "chore",
        "add",
        "Trash",
        "-o",
        "m-f:Morning",
        "-o",
        "m-s:Evening",
    ) == 0
    assert "Added Trash with 11 active occurrence(s)." in capsys.readouterr().out

    assert invoke(database, "chore", "list") == 0
    listed = capsys.readouterr().out
    assert listed.count("Trash: ") == 1
    assert "Monday:Morning" in listed
    assert "Friday:Morning" in listed
    assert "Saturday:Evening" in listed
    assert "Sunday:Evening" not in listed


def test_availability_accepts_weekday_range(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"
    invoke(database, "person", "add", "Alex")

    assert invoke(database, "availability", "set", "Alex", "--slot", "m-f:08:00-18:00") == 0
    assert invoke(database, "person", "list") == 0

    listed = capsys.readouterr().out
    assert all(f"{day}:08:00-18:00" in listed for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"))
    assert "Saturday:08:00-18:00" not in listed


def test_availability_edit_opens_grid_saves_changes_and_deletes_temp_file(tmp_path, capsys, monkeypatch):
    database = tmp_path / "family.sqlite3"
    invoke(database, "person", "add", "Alex")
    invoke(database, "person", "add", "Sam")
    invoke(database, "chore", "add", "Trash", "-o", "m:Morning@17:00-18:00", "-o", "t:Evening@18:00-19:00")
    invoke(database, "availability", "set", "Alex", "--slot", "m:16:00-20:00")
    invoke(database, "availability", "set", "Sam", "--slot", "f:16:00-20:00")
    monkeypatch.setenv("EDITOR", "test-editor")
    monkeypatch.delenv("VISUAL", raising=False)
    temporary_paths = []

    def edit_file(command, check):
        temporary_path = Path(command[-1])
        temporary_paths.append(temporary_path)
        original = temporary_path.read_text(encoding="utf-8")
        assert "Time" in original and "16:00-20:00" in original
        temporary_path.write_text(
            "Time | M | Tu | W | Th | F | Sa | Su\n"
            "17:00-18:00 | | x | | | | |\n"
            "18:00-19:00 | | | x | | | |\n"
            "19:00-21:00 | x | | | | | |\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli_module.subprocess, "run", edit_file)

    assert invoke(database, "availability", "edit", "Alex") == 0
    assert len(temporary_paths) == 1
    assert not temporary_paths[0].exists()
    assert invoke(database, "person", "list") == 0
    listed = capsys.readouterr().out
    assert "Alex: Monday:19:00-21:00, Tuesday:17:00-18:00, Wednesday:18:00-19:00" in listed
    assert "Sam: Friday:16:00-20:00" in listed


def test_availability_all_sets_one_person_to_every_known_slot(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"
    invoke(database, "person", "add", "Alex")
    invoke(database, "person", "add", "Sam")
    invoke(database, "chore", "add", "Trash", "-o", "m:Morning@17:00-18:00", "-o", "t:Evening@18:00-19:00")
    invoke(database, "availability", "set", "Alex", "--slot", "m:16:00-20:00")
    invoke(database, "availability", "set", "Sam", "--slot", "f:16:00-20:00")
    assert invoke(database, "availability", "all", "Alex") == 0
    assert invoke(database, "person", "list") == 0
    listed = capsys.readouterr().out
    alex_line = next(line for line in listed.splitlines() if line.startswith("Alex: "))
    assert all(f"{day}:00:00-24:00" in alex_line for day in (
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    ))
    assert "Sam: Friday:16:00-20:00" in listed


def test_readding_chore_adds_days_without_removing_existing_occurrences(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"

    assert invoke(database, "chore", "add", "Trash", "-o", "m:Anytime") == 0
    assert invoke(database, "chore", "add", "Trash", "-o", "m-sa:Anytime") == 0
    assert "6 active occurrence(s)" in capsys.readouterr().out

    assert invoke(database, "chore", "list") == 0
    listed = capsys.readouterr().out
    assert all(f"{day}:Anytime" in listed for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"))
    assert "Sunday:Anytime" not in listed


def test_removing_occurrence_keeps_other_days_and_del_removes_chore(tmp_path, capsys):
    database = tmp_path / "family.sqlite3"
    invoke(database, "chore", "add", "Trash", "-o", "m-sa:Anytime")

    assert invoke(database, "chore", "rm", "Trash", "-o", "w:Anytime") == 0
    assert invoke(database, "chore", "list") == 0
    listed = capsys.readouterr().out
    assert "Trash:" in listed
    assert "Wednesday:Anytime" not in listed
    assert "Tuesday:Anytime" in listed

    assert invoke(database, "chore", "del", "Trash") == 0
    assert "Removed chore Trash." in capsys.readouterr().out
    assert invoke(database, "chore", "list") == 0
    assert capsys.readouterr().out == ""


def test_different_chore_names_can_share_label_in_export(tmp_path):
    database = tmp_path / "family.sqlite3"
    output = tmp_path / "week.csv"
    invoke(database, "person", "add", "Alex")
    invoke(database, "person", "add", "Sam")
    invoke(database, "availability", "set", "Alex", "--slot", "m:00:00-24:00")
    invoke(database, "availability", "set", "Sam", "--slot", "m:00:00-24:00")

    assert invoke(database, "chore", "add", "trash_alex", "--label", "Trash", "-o", "m:anytime") == 0
    assert invoke(database, "chore", "add", "trash_sam", "--label", "Trash", "-o", "m:anytime") == 0
    assert invoke(database, "schedule", "generate", "--seed", "2", "--output", str(output)) == 0

    rows = list(csv.reader(output.open(encoding="utf-8-sig", newline="")))
    assert [row[0] for row in rows[1:]] == ["Trash (anytime)", "Trash (anytime)"]
    assert {row[1] for row in rows[1:]} == {"Alex", "Sam"}