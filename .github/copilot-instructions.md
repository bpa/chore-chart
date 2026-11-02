# Python Environment

- Run all Python commands with the project virtual environment in `.venv`.
- On Windows, use `.venv\Scripts\python.exe`; on other platforms, use `.venv/bin/python`.
- Invoke tools as modules through that interpreter, such as `.venv\Scripts\python.exe -m pytest` on Windows.
- Do not fall back to a globally installed Python if the project interpreter is unavailable; report the missing environment instead.

# Database Inspection

- Use the `sqlite3` command-line tool to inspect or query SQLite databases; do not write ad hoc Python scripts for database reads.
- Open databases read-only, for example: `sqlite3 -readonly chore_chart.sqlite3 "SELECT name FROM sqlite_master WHERE type = 'table';"`.
- If the `sqlite3` command-line tool is unavailable, report that rather than switching to a Python script.