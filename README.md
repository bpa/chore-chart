# Family Chore Chart

A local web app for managing household chores and generating balanced weekly schedules. FastAPI serves a React frontend and API, backed by SQLite and the constraint-based scheduler.

## Web App

Install the Python package and development tools from the project root:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Build the React frontend, then run `api.bat`. The batch file checks for `frontend/dist` and prints these build commands if it is missing:

```powershell
cd frontend
npm install
npm run build
```

The batch launcher uses `src/chore_chart.sqlite3` so the web app shares the existing project data. Open `http://127.0.0.1:8000/`; API documentation is at `http://127.0.0.1:8000/docs`.

For frontend development with hot reload, run the API and Vite separately:

```powershell
.venv\Scripts\python.exe -m uvicorn chore_chart.api:app --reload
```

```powershell
cd frontend
npm install
npm run dev
```

The development server proxies `/api` requests to FastAPI on port 8000. The interface has People, Chores, Limits, and Charts tabs. Chores have an ordered section, a display label, an order within the section, selected weekdays, and time windows. Section names and order are editable. Availability is stored per person/day/time range and does not depend on a section.

Schedule assignments reference chore, person, and section IDs. Names, labels, and ordering are loaded from those records, so renaming a section updates saved charts as well. Time ranges are local 24-hour values within one day. Charts can be saved, exported as CSV, and printed as a weekly table.

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
