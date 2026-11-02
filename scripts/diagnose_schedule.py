from __future__ import annotations

import argparse
import sqlite3
import tempfile
import time
import traceback
from pathlib import Path

from ortools.sat.python import cp_model

from chore_chart.scheduler import _is_available, generate_schedule
from chore_chart.storage import SQLiteRepository


def _copy_database(source_path: Path, destination_path: Path) -> None:
    with sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True) as source:
        with sqlite3.connect(destination_path) as destination:
            source.backup(destination)


def _trace_solver(feasibility_only: bool) -> tuple[object, object]:
    original_solve = cp_model.CpSolver.solve

    def diagnostic_solve(solver, model, *args, **kwargs):
        if feasibility_only:
            model.clear_objective()
            model.proto.clear_floating_point_objective()
        coefficients = (
            model.proto.floating_point_objective.coeffs
            if model.proto.has_floating_point_objective()
            else ()
        )
        print("CP-SAT model:")
        print(f"  objective mode: {'feasibility only' if feasibility_only else 'scheduler objective'}")
        print(f"  variables: {len(model.proto.variables)}")
        print(f"  constraints: {len(model.proto.constraints)}")
        print(f"  validation: {model.validate() or 'valid'}")
        if coefficients:
            print(f"  floating objective terms: {len(coefficients)}")
            print(f"  largest absolute coefficient: {max(abs(value) for value in coefficients):.3e}")
        result = original_solve(solver, model, *args, **kwargs)
        print(f"  solver status: {solver.status_name(result)}")
        print(f"  solver wall time: {solver.wall_time:.2f}s")
        return result

    cp_model.CpSolver.solve = diagnostic_solve
    return original_solve, diagnostic_solve


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose schedule generation for a SQLite database.")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "chore_chart.sqlite3",
        help="SQLite database to inspect (defaults to the project-root database).",
    )
    parser.add_argument(
        "--feasibility-only",
        action="store_true",
        help="Ignore the scheduler objective and test only whether the constraints are feasible.",
    )
    args = parser.parse_args()
    database_path = args.database.expanduser().resolve()
    if not database_path.is_file():
        parser.error(f"database does not exist: {database_path}")

    print(f"Database: {database_path}")
    started = time.perf_counter()
    repository = None
    original_solve, diagnostic_solve = _trace_solver(args.feasibility_only)
    try:
        with tempfile.TemporaryDirectory(prefix="chore-chart-diagnostic-") as temp_dir:
            snapshot_path = Path(temp_dir) / "snapshot.sqlite3"
            _copy_database(database_path, snapshot_path)
            repository = SQLiteRepository(snapshot_path)
            try:
                people = repository.list_people()
                chores = repository.list_chores()
                rules = repository.list_rules()
                history = repository.recent_schedules(4)
                occurrences = [occurrence for chore in chores for occurrence in chore.occurrences]
                eligible_choices = sum(
                    _is_available(person, occurrence)
                    for person in people
                    for occurrence in occurrences
                )

                print(f"People: {len(people)}")
                print(f"Chores: {len(chores)}")
                print(f"Occurrences: {len(occurrences)}")
                print(f"Assignment rules: {len(rules)}")
                print(f"Recent schedules loaded: {len(history)}")
                print(f"Eligible person-occurrence choices: {eligible_choices}")
                print("People with busy times:", ", ".join(
                    f"{person.name} ({len(person.busy_times)})"
                    for person in people
                    if person.busy_times
                ) or "none")

                schedule = generate_schedule(
                    people,
                    chores,
                    rules,
                    history=history,
                    seed=1,
                )
                print(f"Schedule generated: {len(schedule.assignments)} assignments")
                return 0
            finally:
                repository.close()
                repository = None
    except Exception as error:
        print(f"Generation failed: {type(error).__name__}: {error}")
        traceback.print_exc()
        return 1
    finally:
        cp_model.CpSolver.solve = original_solve
        if repository is not None:
            repository.close()
        print(f"Elapsed: {time.perf_counter() - started:.2f}s")


if __name__ == "__main__":
    raise SystemExit(main())