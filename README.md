# Family Chore Chart

A local Python library and command-line tool for storing household chores in SQLite, generating constraint-based weekly assignments, and exporting a chore-by-weekday CSV matrix.

## Setup

Use the repository's `.venv` for Python commands. From the project directory, install the package and development tools:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

The database defaults to `chore_chart.sqlite3` in the current directory. Set `CHORE_CHART_DB` or pass `--database` before the subcommand to select another path.

## Create a Schedule

Availability is entered as weekday and clock-time windows. Chore occurrences keep a grouping label and may also specify a clock-time range. A person is eligible for a timed chore when their availability overlaps its range by at least one hour. Untimed chores have no time-based availability restriction. New people and migrated databases start with all-day availability; adjust each person's windows as needed. Use inclusive weekday ranges: `m-f` expands Monday through Friday, `m-s` Monday through Saturday, and `f-m` Friday through Monday. Tokens are `m`, `t`/`tu`, `w`, `th`, `f`, `s`/`sa`, and `su`.

```powershell
.venv\Scripts\python.exe -m chore_chart person add "Alex Smith"
.venv\Scripts\python.exe -m chore_chart person add "Sam Smith"

.venv\Scripts\python.exe -m chore_chart availability set "Alex Smith" --slot m-f:16:00-20:00 --slot w:08:00-12:00
.venv\Scripts\python.exe -m chore_chart availability set "Sam Smith" --slot m-f:16:00-20:00 --slot w:08:00-12:00

.venv\Scripts\python.exe -m chore_chart chore add Dishes -o m-f:Morning@17:00-18:00 -o w:Evening@18:00-19:00
.venv\Scripts\python.exe -m chore_chart rule set "Alex Smith" --min 1 --max 2
.venv\Scripts\python.exe -m chore_chart rule set "Sam Smith" --min 1 --max 2

.venv\Scripts\python.exe -m chore_chart schedule generate --seed 42 --output this-week.csv
```

Each chore's first argument is its unique internal name. Repeating `chore add` with that name adds any new weekday/time occurrences without removing existing ones; duplicate occurrences are ignored. Use `--label` to give different chores the same printable name. For example, `trash_alex` and `trash_sam` can both use `--label Trash` while remaining separate assignments.

```powershell
.venv\Scripts\python.exe -m chore_chart chore add trash_alex --label Trash -o m-sa:Anytime@17:00-18:00
.venv\Scripts\python.exe -m chore_chart chore add trash_sam --label Trash -o m-sa:Anytime@18:00-19:00
.venv\Scripts\python.exe -m chore_chart chore rm trash_alex -o w:Anytime@17:00-18:00
.venv\Scripts\python.exe -m chore_chart chore del trash_sam
```

Use `DAY:GROUP@START-END` for timed chore occurrences, with 24-hour `HH:MM` values; ranges must be within the same day. Omit `@START-END` for an untimed occurrence. `chore rm NAME -o DAY:GROUP[@START-END]` removes selected occurrence(s); if none remain, the chore is removed. `chore rm NAME` and its alias `chore del NAME` remove the whole chore.

Commands are grouped under `person`, `availability`, `chore`, `rule`, and `schedule`. Run `.venv\Scripts\python.exe -m chore_chart --help` or add `--help` after a group to see options.

`person list`, `chore list`, and `rule list` show saved configuration. `availability clear NAME` clears all availability for one person. `availability edit NAME` opens that person's table in `$VISUAL` or `$EDITOR` (Notepad on Windows, `vi` elsewhere if neither is set); mark available cells with `x`, and add or remove `HH:MM-HH:MM` rows as needed. The editor must wait until the file is closed, and the VS Code command is automatically given `--wait`. `availability all NAME` sets that person to all-day availability on every weekday. `schedule list` shows saved schedules, and `schedule export ID --output FILE.csv` exports an earlier one.

## Assignment Behavior

Each scheduled chore occurrence is assigned to exactly one eligible person. Per-person weekly minimums and maximums are hard constraints; omitted rules mean a minimum of zero and no maximum. If no valid chart exists, generation reports the constraint problem rather than relaxing a rule.

Among valid charts, the solver first reduces the difference between the highest and lowest number of assignments. It also enforces that a person receives at most one chore with the same display label per weekday. Next it minimizes repeated assignments of each label to the same person during the week, then favors people with a lower assignment count across the four most recent saved schedules and avoids repeating the same person/chore pairing. `--seed` makes the tie-breaking reproducible for the same saved configuration and history.

Generated schedules are saved as immutable snapshots for later review and rotation. The CSV has Monday through Sunday columns, one row per chore/time label, and the assigned person's name in each scheduled day cell. Output is UTF-8 with a BOM for spreadsheet compatibility; blank cells indicate that the chore is not scheduled on that weekday.

## Tests and Coverage

Install the development extra, then run the test suite:

```powershell
.venv\Scripts\python.exe -m pytest
```

Pytest enforces at least 75% total coverage. The core domain and scheduling modules are expected to remain at or above 90%; run the additional core gate with:

```powershell
./scripts/check_coverage.ps1
```

The core gate runs the full suite and checks branch coverage for `domain.py` and `scheduler.py` separately.
