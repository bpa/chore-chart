from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from chore_chart.csv_export import write_schedule_csv
from chore_chart.domain import BusyBlock, ChoreOccurrence, Section, TimeRange, Weekday
from chore_chart.storage import SQLiteRepository


class PersonCreate(BaseModel):
    name: str


class PersonCellStylePayload(BaseModel):
    cell_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    cell_color_mid: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    cell_color_end: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    cell_fill: Literal[
        "solid",
        "linear-horizontal",
        "linear-vertical",
        "linear-diagonal-down",
        "linear-diagonal-up",
        "radial",
    ]


class OccurrencePayload(BaseModel):
    weekday: str
    time_range: str | None = None


class ChoreCreate(BaseModel):
    name: str
    label: str | None = None
    section_id: int
    occurrences: list[OccurrencePayload] = Field(default_factory=list)


class SectionCreate(BaseModel):
    name: str


class SectionOrderPayload(BaseModel):
    section_ids: list[int]


class ChoreOrderPayload(BaseModel):
    chore_ids: list[int]


class BusyBlockPayload(BaseModel):
    weekday: str
    time_range: str
    label: str | None = None


class SetBusyTimesPayload(BaseModel):
    blocks: list[BusyBlockPayload] = Field(default_factory=list)


class AssignmentRulePayload(BaseModel):
    minimum: int = Field(default=0, ge=0)
    maximum: int | None = Field(default=None, ge=0)


class GenerateSchedulePayload(BaseModel):
    seed: int | None = None


class ScheduleAssignmentPayload(BaseModel):
    assignment_id: int = Field(ge=1)
    person_id: int = Field(ge=1)


class ScheduleAssignmentsPayload(BaseModel):
    assignments: list[ScheduleAssignmentPayload]


app = FastAPI(title="Chore Chart API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_repository() -> SQLiteRepository:
    database_path = os.environ.get("CHORE_CHART_DB", "chore_chart.sqlite3")
    return SQLiteRepository(database_path)


def _serialize_person(person: Any) -> dict[str, Any]:
    return {
        "person_id": person.person_id,
        "name": person.name,
        "cell_color": person.cell_color,
        "cell_color_mid": person.cell_color_mid,
        "cell_color_end": person.cell_color_end,
        "cell_fill": person.cell_fill,
        "busy_times": [
            {"weekday": block.weekday.value, "time_range": str(block.time_range), "label": block.label}
            for block in sorted(
                person.busy_times,
                key=lambda item: (
                    item.weekday.order,
                    item.time_range.start_minute,
                    item.time_range.end_minute,
                    item.label or "",
                ),
            )
        ],
    }


def _serialize_chore(chore: Any) -> dict[str, Any]:
    return {
        "chore_id": chore.chore_id,
        "name": chore.name,
        "label": chore.label,
        "section_id": chore.section_id,
        "section_name": chore.section_name,
        "section_order": chore.section_order,
        "order_index": chore.order_index,
        "occurrences": [
            {
                "weekday": occurrence.weekday.value,
                "time_range": str(occurrence.time_range) if occurrence.time_range else None,
            }
            for occurrence in chore.occurrences
        ],
    }


def _serialize_schedule(schedule: Any) -> dict[str, Any]:
    created_at = schedule.created_at
    if created_at is not None and created_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=None)
    return {
        "schedule_id": schedule.schedule_id,
        "seed": schedule.seed,
        "created_at": created_at.isoformat() if created_at else None,
        "assignments": [
            {
                "chore_id": assignment.occurrence.chore_id,
                "weekday": assignment.occurrence.weekday.value,
                "time_range": str(assignment.occurrence.time_range)
                if assignment.occurrence.time_range
                else None,
                "chore": assignment.occurrence.chore_name,
                "label": assignment.occurrence.chore_label
                or assignment.occurrence.chore_name,
                "section_id": assignment.occurrence.section_id,
                "section": assignment.occurrence.section_name,
                "section_order": assignment.occurrence.section_order,
                "chore_order": assignment.occurrence.chore_order,
                "assignment_id": assignment.assignment_id,
                "person_id": assignment.person_id,
                "person": assignment.person_name,
            }
            for assignment in schedule.assignments
        ],
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/people")
def list_people() -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [_serialize_person(person) for person in repository.list_people()]
    finally:
        repository.close()


@app.post("/api/people")
def create_person(payload: PersonCreate) -> dict[str, str]:
    repository = get_repository()
    try:
        person = repository.add_person(payload.name)
        return {"name": person.name}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/people/{person_id}/cell-style")
def update_person_cell_style(
    person_id: int,
    payload: PersonCellStylePayload,
) -> dict[str, Any]:
    repository = get_repository()
    try:
        repository.set_person_cell_style(
            person_id,
            payload.cell_color,
            payload.cell_color_end,
            payload.cell_fill,
            payload.cell_color_mid,
        )
        person = next(
            person for person in repository.list_people() if person.person_id == person_id
        )
        return _serialize_person(person)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/people/{person_name}/busy-times")
def update_busy_times(
    person_name: str,
    payload: SetBusyTimesPayload,
) -> dict[str, Any]:
    repository = get_repository()
    try:
        blocks = [
            BusyBlock(
                Weekday.parse(item.weekday),
                TimeRange.parse(item.time_range),
                item.label,
            )
            for item in payload.blocks
        ]
        repository.set_busy_times(person_name, blocks)
        person = next(
            person for person in repository.list_people() if person.name == person_name
        )
        return _serialize_person(person)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.get("/api/chores")
def list_chores() -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [_serialize_chore(chore) for chore in repository.list_chores()]
    finally:
        repository.close()


@app.get("/api/sections")
def list_sections() -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [
            {"section_id": section.section_id, "name": section.name, "order_index": section.order_index}
            for section in repository.list_sections()
        ]
    finally:
        repository.close()


@app.post("/api/sections")
def create_section(payload: SectionCreate) -> dict[str, Any]:
    repository = get_repository()
    try:
        section = repository.add_section(payload.name)
        return {"section_id": section.section_id, "name": section.name, "order_index": section.order_index}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/sections/order")
def reorder_sections(payload: SectionOrderPayload) -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [
            {"section_id": section.section_id, "name": section.name, "order_index": section.order_index}
            for section in repository.reorder_sections(payload.section_ids)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/sections/{section_id}")
def update_section(section_id: int, payload: SectionCreate) -> dict[str, Any]:
    repository = get_repository()
    try:
        current = repository.get_section(section_id)
        section = repository.update_section(section_id, payload.name, current.order_index)
        return {"section_id": section.section_id, "name": section.name, "order_index": section.order_index}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/sections/{section_id}/chores/order")
def reorder_section_chores(section_id: int, payload: ChoreOrderPayload) -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [
            _serialize_chore(chore)
            for chore in repository.reorder_chores(section_id, payload.chore_ids)
            if chore.section_id == section_id
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.post("/api/chores")
def create_chore(payload: ChoreCreate) -> dict[str, Any]:
    repository = get_repository()
    try:
        section = repository.get_section(payload.section_id)
        occurrences = tuple(
            ChoreOccurrence(
                payload.name,
                Weekday.parse(item.weekday),
                section.name,
                time_range=TimeRange.parse(item.time_range) if item.time_range else None,
            )
            for item in payload.occurrences
        )
        chore = repository.add_chore(
            payload.name,
            occurrences,
            label=payload.label,
            section_id=section.section_id,
            section_name=section.name,
        )
        return _serialize_chore(chore)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/chores/{chore_id}")
def update_chore(chore_id: int, payload: ChoreCreate) -> dict[str, Any]:
    repository = get_repository()
    try:
        section = repository.get_section(payload.section_id)
        occurrences = tuple(
            ChoreOccurrence(
                payload.name,
                Weekday.parse(item.weekday),
            section.name,
                time_range=TimeRange.parse(item.time_range) if item.time_range else None,
            )
            for item in payload.occurrences
        )
        chore = repository.update_chore(
            chore_id,
            occurrences,
            label=payload.label,
            section_id=section.section_id,
            section_name=section.name,
        )
        return _serialize_chore(chore)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.delete("/api/chores/{chore_id}")
def delete_chore(chore_id: int) -> dict[str, Any]:
    repository = get_repository()
    try:
        chore = next(chore for chore in repository.list_chores() if chore.chore_id == chore_id)
        removed = repository.remove_chore(chore_id)
        return {"chore_id": chore_id, "name": chore.name, "removed_occurrences": removed}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        repository.close()


@app.get("/api/rules")
def list_rules() -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [
            {
                "person": rule.person_name,
                "minimum": rule.minimum,
                "maximum": rule.maximum,
            }
            for rule in repository.list_rules()
        ]
    finally:
        repository.close()


@app.put("/api/rules/{person_name}")
def update_rule(person_name: str, payload: AssignmentRulePayload) -> dict[str, Any]:
    repository = get_repository()
    try:
        rule = repository.set_rule(person_name, payload.minimum, payload.maximum)
        return {
            "person": rule.person_name,
            "minimum": rule.minimum,
            "maximum": rule.maximum,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.post("/api/schedules/generate")
def generate_schedule(payload: GenerateSchedulePayload) -> dict[str, Any]:
    repository = get_repository()
    try:
        schedule = repository.generate_and_save(seed=payload.seed)
        return _serialize_schedule(schedule)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.get("/api/schedules")
def list_schedules(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    repository = get_repository()
    try:
        return [_serialize_schedule(schedule) for schedule in repository.list_schedules(limit)]
    finally:
        repository.close()


@app.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: int) -> dict[str, Any]:
    repository = get_repository()
    try:
        return _serialize_schedule(repository.get_schedule(schedule_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        repository.close()


@app.put("/api/schedules/{schedule_id}/assignments")
def update_schedule_assignments(
    schedule_id: int,
    payload: ScheduleAssignmentsPayload,
) -> dict[str, Any]:
    repository = get_repository()
    try:
        schedule = repository.update_schedule_assignments(
            schedule_id,
            [(item.assignment_id, item.person_id) for item in payload.assignments],
        )
        return _serialize_schedule(schedule)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        repository.close()


@app.get("/api/schedules/{schedule_id}/csv")
def download_schedule_csv(schedule_id: int) -> Response:
    repository = get_repository()
    try:
        schedule = repository.get_schedule(schedule_id)
        output = StringIO(newline="")
        write_schedule_csv(schedule, output)
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="schedule-{schedule_id}.csv"'
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        repository.close()


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
app.mount(
    "/",
    StaticFiles(directory=frontend_dist, html=True, check_dir=False),
    name="frontend",
)
