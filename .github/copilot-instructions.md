# Python Environment

- Run all Python commands with the project virtual environment in `.venv`.
- On Windows, use `.venv\Scripts\python.exe`; on other platforms, use `.venv/bin/python`.
- Invoke tools as modules through that interpreter, such as `.venv\Scripts\python.exe -m pytest` on Windows.
- Do not fall back to a globally installed Python if the project interpreter is unavailable; report the missing environment instead.