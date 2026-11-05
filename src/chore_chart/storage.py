from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, create_engine, delete, event, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from chore_chart.domain import (
    Assignment,
    AssignmentRule,
    BusyBlock,
    Chore,
    ChoreAssignmentRule,
    ChoreOccurrence,
    Person,
    Schedule,
    Section,
    TimeRange,
    Weekday,
    normalize_chore_text,
)


class Base(DeclarativeBase):
    pass


_SCHEMA_LOCK = RLock()


class PersonRecord(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    cell_color: Mapped[str] = mapped_column(String(7), default="#e7f1ec")
    cell_color_mid: Mapped[str | None] = mapped_column(String(7), nullable=True)
    cell_color_end: Mapped[str] = mapped_column(String(7), default="#dce8f1")
    cell_fill: Mapped[str] = mapped_column(String(8), default="solid")
    cell_text_color: Mapped[str] = mapped_column(String(7), default="#202c29")


class BusyTimeRecord(Base):
    __tablename__ = "availability"
    __table_args__ = (
        UniqueConstraint("person_id", "weekday", "start_minute", "end_minute"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    weekday: Mapped[str] = mapped_column(String(9))
    start_minute: Mapped[int] = mapped_column(Integer, default=0)
    end_minute: Mapped[int] = mapped_column(Integer, default=1440)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)


class SectionRecord(Base):
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)


class ChoreRecord(Base):
    __tablename__ = "chores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"))
    order_index: Mapped[int] = mapped_column(Integer, default=0)


class OccurrenceRecord(Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        UniqueConstraint("chore_id", "weekday", "start_minute", "end_minute"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chore_id: Mapped[int] = mapped_column(ForeignKey("chores.id", ondelete="CASCADE"))
    weekday: Mapped[str] = mapped_column(String(9))
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RuleRecord(Base):
    __tablename__ = "assignment_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), unique=True)
    minimum: Mapped[int] = mapped_column(default=0)
    maximum: Mapped[int | None]


class ChoreAssignmentRuleRecord(Base):
    __tablename__ = "chore_assignment_rules"
    __table_args__ = (UniqueConstraint("person_id", "chore_id", "weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    chore_id: Mapped[int] = mapped_column(ForeignKey("chores.id", ondelete="CASCADE"))
    weekday: Mapped[str] = mapped_column(String(9))
    is_assigned: Mapped[bool]


class ScheduleRecord(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    seed: Mapped[int | None]


class AssignmentRecord(Base):
    __tablename__ = "schedule_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    chore_id: Mapped[int] = mapped_column(ForeignKey("chores.id"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"))
    weekday: Mapped[str] = mapped_column(String(9))
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SQLiteRepository:
    def __init__(self, database_path: str | Path = "chore_chart.sqlite3") -> None:
        path = Path(database_path).expanduser()
        if str(database_path) != ":memory:":
            path = path.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path.as_posix()}"
        else:
            url = "sqlite:///:memory:"
        self._engine = create_engine(url, future=True)
        self._enable_foreign_keys(self._engine)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)
        with _SCHEMA_LOCK:
            Base.metadata.create_all(self._engine)
            self._upgrade_schema()

    def _upgrade_schema(self) -> None:
        with self._engine.begin() as connection:
            tables = set(inspect(connection).get_table_names())
            if "people" in tables:
                people_columns = {
                    column["name"] for column in inspect(connection).get_columns("people")
                }
                if "cell_color" not in people_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE people ADD COLUMN cell_color VARCHAR(7) NOT NULL DEFAULT '#e7f1ec'"
                    )
                if "cell_color_end" not in people_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE people ADD COLUMN cell_color_end VARCHAR(7) NOT NULL DEFAULT '#dce8f1'"
                    )
                if "cell_color_mid" not in people_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE people ADD COLUMN cell_color_mid VARCHAR(7)"
                    )
                if "cell_fill" not in people_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE people ADD COLUMN cell_fill VARCHAR(8) NOT NULL DEFAULT 'solid'"
                    )
                if "cell_text_color" not in people_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE people ADD COLUMN cell_text_color VARCHAR(7) NOT NULL DEFAULT '#202c29'"
                    )
            if "chores" not in tables:
                return

            columns = {
                table: {column["name"] for column in inspect(connection).get_columns(table)}
                for table in ("chores", "availability", "occurrences", "schedule_assignments")
            }
            chore_additions = {
                "label": "VARCHAR(160) NOT NULL DEFAULT ''",
                "section_id": "INTEGER REFERENCES sections(id)",
                "order_index": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in chore_additions.items():
                if name not in columns["chores"]:
                    connection.exec_driver_sql(f"ALTER TABLE chores ADD COLUMN {name} {definition}")
            if connection.exec_driver_sql(
                "SELECT 1 FROM chores WHERE label IS NULL OR label = '' LIMIT 1"
            ).first():
                connection.exec_driver_sql("UPDATE chores SET label = name WHERE label IS NULL OR label = ''")

            range_missing = not {"start_minute", "end_minute"}.issubset(columns["availability"])
            labels_missing = "label" not in columns["availability"]
            for table in ("availability", "occurrences", "schedule_assignments"):
                for name in ("start_minute", "end_minute"):
                    if name not in columns[table]:
                        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} INTEGER")
            if labels_missing:
                connection.exec_driver_sql("ALTER TABLE availability ADD COLUMN label VARCHAR(160)")
                connection.exec_driver_sql("DELETE FROM availability")
            elif "time_label" in columns["availability"]:
                connection.exec_driver_sql("DELETE FROM availability")

            if "time_label" not in columns["occurrences"]:
                return

            chore_rows = connection.exec_driver_sql(
                "SELECT id, name, label FROM chores ORDER BY id"
            ).all()
            normalized_names = [normalize_chore_text(row[1]) for row in chore_rows]
            if len(normalized_names) != len(set(normalized_names)):
                raise ValueError("Quote normalization would merge existing chore names.")
            for (chore_id, name, label), normalized_name in zip(chore_rows, normalized_names):
                connection.exec_driver_sql(
                    "UPDATE chores SET name = ?, label = ? WHERE id = ?",
                    (normalized_name, normalize_chore_text(label or name), chore_id),
                )
            chore_rows = [
                (chore_id, normalized_name, normalize_chore_text(label or name))
                for (chore_id, name, label), normalized_name in zip(chore_rows, normalized_names)
            ]

            legacy_occurrences = connection.exec_driver_sql(
                "SELECT id, chore_id, weekday, time_label, start_minute, end_minute "
                "FROM occurrences ORDER BY chore_id, id"
            ).all()
            legacy_by_chore: dict[int, list[Any]] = {}
            for occurrence in legacy_occurrences:
                legacy_by_chore.setdefault(occurrence[1], []).append(occurrence)

            section_by_name: dict[str, tuple[int, str, int]] = {}
            for occurrence in legacy_occurrences:
                section_name = normalize_chore_text(occurrence[3])
                section_key = section_name.casefold()
                if section_key not in section_by_name:
                    section_order = len(section_by_name)
                    connection.exec_driver_sql(
                        "INSERT INTO sections (name, order_index) VALUES (?, ?)",
                        (section_name, section_order),
                    )
                    section_id = connection.exec_driver_sql("SELECT last_insert_rowid()").scalar_one()
                    section_by_name[section_key] = (section_id, section_name, section_order)

            used_names = {normalize_chore_text(row[1]) for row in chore_rows}
            section_chore_ids: dict[tuple[int, str], int] = {}
            chore_order_by_section: dict[int, int] = {}
            for chore_id, chore_name, chore_label in chore_rows:
                groups: dict[str, list[Any]] = {}
                for occurrence in legacy_by_chore.get(chore_id, []):
                    group_key = normalize_chore_text(occurrence[3]).casefold()
                    groups.setdefault(group_key, []).append(occurrence)
                if not groups:
                    raise ValueError(f"Chore {chore_name!r} has no occurrences to assign to a section.")
                for group_index, group_key in enumerate(groups):
                    section_id, section_name, section_order = section_by_name[group_key]
                    chore_order = chore_order_by_section.get(section_id, 0)
                    chore_order_by_section[section_id] = chore_order + 1
                    target_id = chore_id
                    if group_index:
                        suffix = f" [{section_name}]"
                        clone_name = f"{chore_name[:160 - len(suffix)]}{suffix}"
                        collision_index = 2
                        candidate = clone_name
                        while candidate in used_names:
                            marker = f" [{section_name} {collision_index}]"
                            candidate = f"{chore_name[:160 - len(marker)]}{marker}"
                            collision_index += 1
                        clone_name = candidate
                        used_names.add(clone_name)
                        connection.exec_driver_sql(
                            "INSERT INTO chores (name, label, section_id, order_index) VALUES (?, ?, ?, ?)",
                            (clone_name, normalize_chore_text(chore_label or chore_name), section_id, chore_order),
                        )
                        target_id = connection.exec_driver_sql("SELECT last_insert_rowid()").scalar_one()
                    else:
                        connection.exec_driver_sql(
                            "UPDATE chores SET section_id = ?, order_index = ? WHERE id = ?",
                            (section_id, chore_order, chore_id),
                        )
                    section_chore_ids[(chore_id, group_key)] = target_id

            connection.exec_driver_sql(
                "CREATE TABLE occurrences_new ("
                "id INTEGER PRIMARY KEY, chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE, "
                "weekday VARCHAR(9) NOT NULL, start_minute INTEGER, end_minute INTEGER, "
                "CONSTRAINT uq_occurrence_slot UNIQUE (chore_id, weekday, start_minute, end_minute))"
            )
            for occurrence in legacy_occurrences:
                target_id = section_chore_ids[(occurrence[1], normalize_chore_text(occurrence[3]).casefold())]
                connection.exec_driver_sql(
                    "INSERT INTO occurrences_new (id, chore_id, weekday, start_minute, end_minute) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (occurrence[0], target_id, occurrence[2], occurrence[4], occurrence[5]),
                )
            connection.exec_driver_sql("DROP TABLE occurrences")
            connection.exec_driver_sql("ALTER TABLE occurrences_new RENAME TO occurrences")

            availability_rows = connection.exec_driver_sql(
                "SELECT id, person_id, weekday, start_minute, end_minute, label FROM availability"
            ).all()
            connection.exec_driver_sql(
                "CREATE TABLE availability_new ("
                "id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE, "
                "weekday VARCHAR(9) NOT NULL, start_minute INTEGER NOT NULL, end_minute INTEGER NOT NULL, "
                "label VARCHAR(160), "
                "CONSTRAINT uq_availability_window UNIQUE (person_id, weekday, start_minute, end_minute))"
            )
            for availability in availability_rows:
                connection.exec_driver_sql(
                    "INSERT OR IGNORE INTO availability_new "
                    "(id, person_id, weekday, start_minute, end_minute, label) VALUES (?, ?, ?, ?, ?, ?)",
                    tuple(availability),
                )
            connection.exec_driver_sql("DROP TABLE availability")
            connection.exec_driver_sql("ALTER TABLE availability_new RENAME TO availability")

            chore_id_by_name = {normalize_chore_text(row[1]): row[0] for row in chore_rows}
            person_id_by_name = {
                row[1]: row[0]
                for row in connection.exec_driver_sql("SELECT id, name FROM people").all()
            }
            assignment_columns = {
                column["name"] for column in inspect(connection).get_columns("schedule_assignments")
            }
            if "chore_name" in assignment_columns:
                chore_label_column = "chore_label" if "chore_label" in assignment_columns else "NULL"
                legacy_assignments = connection.exec_driver_sql(
                    "SELECT id, schedule_id, chore_name, "
                    f"{chore_label_column}, person_name, weekday, time_label, "
                    "start_minute, end_minute FROM schedule_assignments ORDER BY id"
                ).all()
                connection.exec_driver_sql(
                    "CREATE TABLE schedule_assignments_new ("
                    "id INTEGER PRIMARY KEY, schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE, "
                    "chore_id INTEGER NOT NULL REFERENCES chores(id), person_id INTEGER NOT NULL REFERENCES people(id), "
                    "section_id INTEGER NOT NULL REFERENCES sections(id), weekday VARCHAR(9) NOT NULL, "
                    "start_minute INTEGER, end_minute INTEGER)"
                )
                for assignment in legacy_assignments:
                    old_chore_id = chore_id_by_name.get(normalize_chore_text(assignment[2]))
                    if old_chore_id is None:
                        old_chore_id = chore_id_by_name.get(normalize_chore_text(assignment[3] or ""))
                    section_key = normalize_chore_text(assignment[6]).casefold()
                    target_chore_id = section_chore_ids.get((old_chore_id, section_key))
                    if target_chore_id is None or assignment[4] not in person_id_by_name:
                        raise ValueError("A saved assignment references a missing chore or person.")
                    chore_row = connection.exec_driver_sql(
                        "SELECT section_id FROM chores WHERE id = ?", (target_chore_id,)
                    ).one()
                    connection.exec_driver_sql(
                        "INSERT INTO schedule_assignments_new "
                        "(id, schedule_id, chore_id, person_id, section_id, weekday, start_minute, end_minute) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            assignment[0], assignment[1], target_chore_id,
                            person_id_by_name[assignment[4]], chore_row[0], assignment[5],
                            assignment[7], assignment[8],
                        ),
                    )
                connection.exec_driver_sql("DROP TABLE schedule_assignments")
                connection.exec_driver_sql(
                    "ALTER TABLE schedule_assignments_new RENAME TO schedule_assignments"
                )

    @staticmethod
    def _enable_foreign_keys(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def close(self) -> None:
        self._engine.dispose()

    def list_sections(self) -> list[Section]:
        with self._sessions() as session:
            rows = session.scalars(
                select(SectionRecord).order_by(SectionRecord.order_index, SectionRecord.id)
            ).all()
        return [Section(row.name, row.id, row.order_index) for row in rows]

    def get_section(self, section_id: int) -> Section:
        with self._sessions() as session:
            row = session.get(SectionRecord, section_id)
            if row is None:
                raise ValueError(f"Unknown section {section_id}.")
            return Section(row.name, row.id, row.order_index)

    def add_section(self, name: str) -> Section:
        section = Section(name)
        with self._sessions.begin() as session:
            existing = session.scalars(select(SectionRecord)).all()
            if any(row.name.casefold() == section.name.casefold() for row in existing):
                raise ValueError(f"Section {section.name!r} already exists.")
            order_index = max((row.order_index for row in existing), default=-1) + 1
            record = SectionRecord(name=section.name, order_index=order_index)
            session.add(record)
            session.flush()
            section_id = record.id
        return Section(section.name, section_id, order_index)

    def update_section(self, section_id: int, name: str, order_index: int) -> Section:
        section = Section(name, section_id, order_index)
        with self._sessions.begin() as session:
            records = session.scalars(
                select(SectionRecord).order_by(SectionRecord.order_index, SectionRecord.id)
            ).all()
            record = next((item for item in records if item.id == section_id), None)
            if record is None:
                raise ValueError(f"Unknown section {section_id}.")
            duplicate = session.scalar(
                select(SectionRecord).where(
                    SectionRecord.id != section_id,
                    func.lower(SectionRecord.name) == section.name.casefold(),
                )
            )
            if duplicate is not None:
                raise ValueError(f"Section {section.name!r} already exists.")
            record.name = section.name
            records.remove(record)
            records.insert(min(order_index, len(records)), record)
            for index, item in enumerate(records):
                item.order_index = index
        return Section(section.name, section_id, records.index(record))

    def reorder_sections(self, section_ids: Sequence[int]) -> list[Section]:
        with self._sessions.begin() as session:
            records = session.scalars(select(SectionRecord)).all()
            records_by_id = {record.id: record for record in records}
            if len(section_ids) != len(set(section_ids)) or set(section_ids) != set(records_by_id):
                raise ValueError("Section order must include every section exactly once.")
            for order_index, section_id in enumerate(section_ids):
                records_by_id[section_id].order_index = order_index
        return self.list_sections()

    def reorder_chores(self, section_id: int, chore_ids: Sequence[int]) -> list[Chore]:
        with self._sessions.begin() as session:
            section = session.get(SectionRecord, section_id)
            if section is None:
                raise ValueError(f"Unknown section {section_id}.")
            records = session.scalars(
                select(ChoreRecord).where(ChoreRecord.section_id == section_id)
            ).all()
            records_by_id = {record.id: record for record in records}
            if len(chore_ids) != len(set(chore_ids)) or set(chore_ids) != set(records_by_id):
                raise ValueError("Chore order must include every chore in the section exactly once.")
            for order_index, chore_id in enumerate(chore_ids):
                records_by_id[chore_id].order_index = order_index
        return self.list_chores()

    @staticmethod
    def _get_section(session, section_name: str, section_id: int | None) -> SectionRecord:
        if section_id is not None:
            record = session.get(SectionRecord, section_id)
            if record is None:
                raise ValueError(f"Unknown section {section_id}.")
            return record
        normalized_name = normalize_chore_text(section_name)
        record = session.scalar(
            select(SectionRecord).where(func.lower(SectionRecord.name) == normalized_name.casefold())
        )
        if record is None:
            order_index = session.scalar(select(func.max(SectionRecord.order_index)))
            record = SectionRecord(
                name=normalized_name,
                order_index=(order_index if order_index is not None else -1) + 1,
            )
            session.add(record)
            session.flush()
        return record

    def add_person(self, name: str) -> Person:
        person = Person(name)
        with self._sessions.begin() as session:
            if session.scalar(select(PersonRecord).where(PersonRecord.name == person.name)):
                raise ValueError(f"Person {person.name!r} already exists.")
            record = PersonRecord(name=person.name)
            session.add(record)
            session.flush()
            person_id = record.id
        return Person(name=person.name, person_id=person_id)

    def list_people(self) -> list[Person]:
        with self._sessions() as session:
            records = session.scalars(select(PersonRecord).order_by(PersonRecord.name)).all()
            busy_rows = session.scalars(select(BusyTimeRecord)).all()
        busy_by_person: dict[int, set[BusyBlock]] = {}
        for row in busy_rows:
            busy_by_person.setdefault(row.person_id, set()).add(
                BusyBlock(
                    Weekday.parse(row.weekday),
                    TimeRange(row.start_minute, row.end_minute),
                    row.label,
                )
            )
        return [
            Person(
                name=record.name,
                person_id=record.id,
                busy_times=frozenset(busy_by_person.get(record.id, set())),
                cell_color=record.cell_color,
                cell_color_mid=record.cell_color_mid,
                cell_color_end=record.cell_color_end,
                cell_fill=record.cell_fill,
                cell_text_color=record.cell_text_color,
            )
            for record in records
        ]

    def set_person_cell_style(
        self,
        person_id: int,
        cell_color: str,
        cell_color_end: str,
        cell_fill: str,
        cell_color_mid: str | None,
        cell_text_color: str = "#202c29",
    ) -> None:
        with self._sessions.begin() as session:
            person = session.get(PersonRecord, person_id)
            if person is None:
                raise ValueError(f"Unknown person {person_id}.")
            person.cell_color = cell_color
            person.cell_color_mid = cell_color_mid
            person.cell_color_end = cell_color_end
            person.cell_fill = cell_fill
            person.cell_text_color = cell_text_color

    def set_busy_times(self, person_name: str, blocks: Iterable[BusyBlock]) -> None:
        normalized_blocks = set(blocks)
        with self._sessions.begin() as session:
            person = session.scalar(select(PersonRecord).where(PersonRecord.name == person_name.strip()))
            if person is None:
                raise ValueError(f"Unknown person {person_name!r}.")
            session.execute(delete(BusyTimeRecord).where(BusyTimeRecord.person_id == person.id))
            session.add_all(
                BusyTimeRecord(
                    person_id=person.id,
                    weekday=block.weekday.value,
                    start_minute=block.time_range.start_minute,
                    end_minute=block.time_range.end_minute,
                    label=block.label,
                )
                for block in sorted(
                    normalized_blocks,
                    key=lambda item: (
                        item.weekday.order,
                        item.time_range.start_minute,
                        item.time_range.end_minute,
                        item.label or "",
                    ),
                )
            )

    def clear_busy_times(self, person_name: str) -> None:
        self.set_busy_times(person_name, [])

    def add_chore(
        self,
        name: str,
        occurrences: Sequence[ChoreOccurrence],
        label: str | None = None,
        section_name: str | None = None,
        section_id: int | None = None,
        order_index: int | None = None,
    ) -> Chore:
        chore_name = normalize_chore_text(name)
        if not chore_name:
            raise ValueError("Chore name cannot be empty.")
        if not occurrences:
            raise ValueError("A chore must have at least one scheduled occurrence.")
        chore_label = normalize_chore_text(label) if label is not None else chore_name
        if not chore_label:
            raise ValueError("Chore label cannot be empty.")
        normalized = [
            ChoreOccurrence(
                chore_name=chore_name,
                weekday=occurrence.weekday,
                section_name=section_name or occurrence.section_name,
                chore_label=chore_label,
                time_range=occurrence.time_range,
            )
            for occurrence in occurrences
        ]
        keys = [
            (item.weekday, item.time_range)
            for item in normalized
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("A chore cannot have duplicate occurrences on the same day and time.")

        with self._sessions.begin() as session:
            section = self._get_section(session, section_name or normalized[0].section_name, section_id)
            record = session.scalar(select(ChoreRecord).where(ChoreRecord.name == chore_name))
            if record is None:
                section_chores = session.scalars(
                    select(ChoreRecord)
                    .where(ChoreRecord.section_id == section.id)
                    .order_by(ChoreRecord.order_index, ChoreRecord.id)
                ).all()
                target_order = (
                    min(order_index, len(section_chores))
                    if order_index is not None
                    else len(section_chores)
                )
                for index, existing_chore in enumerate(section_chores):
                    if index >= target_order:
                        existing_chore.order_index = index + 1
                record = ChoreRecord(
                    name=chore_name,
                    label=chore_label,
                    section_id=section.id,
                    order_index=target_order,
                )
                session.add(record)
                session.flush()
            else:
                if record.section_id != section.id:
                    raise ValueError("Use update_chore to move a chore to a different section.")
                if label is not None:
                    record.label = chore_label
                if order_index is not None:
                    record.order_index = order_index
                chore_label = record.label or chore_name

            existing = session.scalars(
                select(OccurrenceRecord).where(OccurrenceRecord.chore_id == record.id)
            ).all()
            existing_keys = {
                (
                    item.weekday,
                    TimeRange(item.start_minute, item.end_minute)
                    if item.start_minute is not None and item.end_minute is not None
                    else None,
                )
                for item in existing
            }
            new_occurrences = [
                item for item in normalized
                if (item.weekday.value, item.time_range) not in existing_keys
            ]
            session.add_all(
                OccurrenceRecord(
                    chore_id=record.id,
                    weekday=occurrence.weekday.value,
                    start_minute=(
                        occurrence.time_range.start_minute if occurrence.time_range else None
                    ),
                    end_minute=occurrence.time_range.end_minute if occurrence.time_range else None,
                )
                for occurrence in new_occurrences
            )
            session.flush()
            all_occurrences = session.scalars(
                select(OccurrenceRecord)
                .where(OccurrenceRecord.chore_id == record.id)
                .order_by(OccurrenceRecord.weekday, OccurrenceRecord.start_minute, OccurrenceRecord.id)
            ).all()
            record_id = record.id

        return Chore(
            name=chore_name,
            label=chore_label,
            chore_id=record_id,
            section_id=section.id,
            section_name=section.name,
            section_order=section.order_index,
            order_index=record.order_index,
            occurrences=tuple(
                ChoreOccurrence(
                    chore_name=chore_name,
                    weekday=Weekday.parse(item.weekday),
                    occurrence_id=item.id,
                    chore_id=record_id,
                    chore_label=chore_label,
                    time_range=(
                        TimeRange(item.start_minute, item.end_minute)
                        if item.start_minute is not None and item.end_minute is not None
                        else None
                    ),
                    section_id=section.id,
                    section_name=section.name,
                    section_order=section.order_index,
                    chore_order=record.order_index,
                )
                for item in sorted(
                    all_occurrences,
                    key=lambda item: (
                        Weekday.parse(item.weekday).order,
                        item.start_minute if item.start_minute is not None else -1,
                    ),
                )
            ),
        )

    def update_chore(
        self,
        chore_id: int,
        occurrences: Sequence[ChoreOccurrence],
        label: str | None = None,
        section_name: str | None = None,
        section_id: int | None = None,
        order_index: int | None = None,
    ) -> Chore:
        if not occurrences:
            raise ValueError("A chore must have at least one scheduled occurrence.")
        with self._sessions.begin() as session:
            record = session.get(ChoreRecord, chore_id)
            if record is None:
                raise ValueError(f"Unknown chore {chore_id}.")
            section = self._get_section(
                session,
                section_name or occurrences[0].section_name,
                section_id,
            )
            chore_name = record.name
            chore_label = normalize_chore_text(label) if label is not None else record.label or chore_name
            if not chore_label:
                raise ValueError("Chore label cannot be empty.")
            normalized = tuple(
                ChoreOccurrence(
                    chore_name=chore_name,
                    weekday=occurrence.weekday,
                    section_name=section.name,
                    chore_label=chore_label,
                    time_range=occurrence.time_range,
                )
                for occurrence in occurrences
            )
            keys = [(item.weekday, item.time_range) for item in normalized]
            if len(keys) != len(set(keys)):
                raise ValueError("A chore cannot have duplicate occurrences on the same day and time.")
            record.label = chore_label
            original_section_id = record.section_id
            record.section_id = section.id
            source_records = session.scalars(
                select(ChoreRecord)
                .where(ChoreRecord.section_id == original_section_id, ChoreRecord.id != record.id)
                .order_by(ChoreRecord.order_index, ChoreRecord.id)
            ).all()
            for index, item in enumerate(source_records):
                item.order_index = index
            destination_records = session.scalars(
                select(ChoreRecord)
                .where(ChoreRecord.section_id == section.id, ChoreRecord.id != record.id)
                .order_by(ChoreRecord.order_index, ChoreRecord.id)
            ).all()
            if order_index is not None:
                target_order = min(order_index, len(destination_records))
            elif original_section_id == section.id:
                target_order = min(record.order_index, len(destination_records))
            else:
                target_order = len(destination_records)
            destination_records.insert(target_order, record)
            record.section_id = section.id
            for index, item in enumerate(destination_records):
                item.order_index = index
            session.execute(delete(OccurrenceRecord).where(OccurrenceRecord.chore_id == record.id))
            session.add_all(
                OccurrenceRecord(
                    chore_id=record.id,
                    weekday=occurrence.weekday.value,
                    start_minute=(occurrence.time_range.start_minute if occurrence.time_range else None),
                    end_minute=(occurrence.time_range.end_minute if occurrence.time_range else None),
                )
                for occurrence in normalized
            )
            session.flush()
            record_id = record.id
            saved_occurrences = session.scalars(
                select(OccurrenceRecord)
                .where(OccurrenceRecord.chore_id == record_id)
                .order_by(OccurrenceRecord.weekday, OccurrenceRecord.start_minute, OccurrenceRecord.id)
            ).all()

        return Chore(
            name=chore_name,
            label=chore_label,
            chore_id=record_id,
            section_id=section.id,
            section_name=section.name,
            section_order=section.order_index,
            order_index=record.order_index,
            occurrences=tuple(
                ChoreOccurrence(
                    chore_name=chore_name,
                    weekday=Weekday.parse(item.weekday),
                    occurrence_id=item.id,
                    chore_id=record_id,
                    chore_label=chore_label,
                    time_range=(
                        TimeRange(item.start_minute, item.end_minute)
                        if item.start_minute is not None and item.end_minute is not None
                        else None
                    ),
                    section_id=section.id,
                    section_name=section.name,
                    section_order=section.order_index,
                    chore_order=record.order_index,
                )
                for item in sorted(
                    saved_occurrences,
                    key=lambda item: (
                        Weekday.parse(item.weekday).order,
                        item.start_minute if item.start_minute is not None else -1,
                    ),
                )
            ),
        )

    def remove_chore(
        self,
        chore_id: int,
        occurrences: Sequence[ChoreOccurrence] | None = None,
    ) -> int:
        with self._sessions.begin() as session:
            chore = session.get(ChoreRecord, chore_id)
            if chore is None:
                raise ValueError(f"Unknown chore {chore_id}.")
            if occurrences is None:
                count = session.scalar(
                    select(func.count())
                    .select_from(OccurrenceRecord)
                    .where(OccurrenceRecord.chore_id == chore.id)
                ) or 0
                session.delete(chore)
                return count

            removed = 0
            for occurrence in occurrences:
                statement = delete(OccurrenceRecord).where(
                    OccurrenceRecord.chore_id == chore.id,
                    OccurrenceRecord.weekday == occurrence.weekday.value,
                )
                if occurrence.time_range is not None:
                    statement = statement.where(
                        OccurrenceRecord.start_minute == occurrence.time_range.start_minute,
                        OccurrenceRecord.end_minute == occurrence.time_range.end_minute,
                    )
                else:
                    statement = statement.where(
                        OccurrenceRecord.start_minute.is_(None),
                        OccurrenceRecord.end_minute.is_(None),
                    )
                result = session.execute(statement)
                removed += result.rowcount or 0
            if not removed:
                raise ValueError(f"No matching occurrence found for chore {chore.name!r}.")

            remaining = session.scalar(
                select(func.count())
                .select_from(OccurrenceRecord)
                .where(OccurrenceRecord.chore_id == chore.id)
            )
            if remaining == 0:
                session.delete(chore)
        return removed

    def list_chores(self) -> list[Chore]:
        with self._sessions() as session:
            chore_rows = session.execute(
                select(ChoreRecord, SectionRecord)
                .join(SectionRecord, ChoreRecord.section_id == SectionRecord.id)
                .order_by(SectionRecord.order_index, ChoreRecord.order_index, ChoreRecord.id)
            ).all()
            occurrence_records = session.scalars(select(OccurrenceRecord)).all()
        by_chore: dict[int, list[OccurrenceRecord]] = {}
        for row in occurrence_records:
            by_chore.setdefault(row.chore_id, []).append(row)
        return [
            Chore(
                name=record.name,
                label=record.label or record.name,
                chore_id=record.id,
                section_id=section.id,
                section_name=section.name,
                section_order=section.order_index,
                order_index=record.order_index,
                occurrences=tuple(
                    ChoreOccurrence(
                        chore_name=record.name,
                        weekday=Weekday.parse(item.weekday),
                        occurrence_id=item.id,
                        chore_id=record.id,
                        chore_label=record.label or record.name,
                        time_range=(
                            TimeRange(item.start_minute, item.end_minute)
                            if item.start_minute is not None and item.end_minute is not None
                            else None
                        ),
                        section_id=section.id,
                        section_name=section.name,
                        section_order=section.order_index,
                        chore_order=record.order_index,
                    )
                    for item in sorted(
                        by_chore.get(record.id, []),
                        key=lambda item: (
                            Weekday.parse(item.weekday).order,
                            item.start_minute if item.start_minute is not None else -1,
                        ),
                    )
                ),
            )
            for record, section in chore_rows
        ]

    def set_rule(self, person_name: str, minimum: int, maximum: int | None) -> AssignmentRule:
        rule = AssignmentRule(person_name, minimum, maximum)
        with self._sessions.begin() as session:
            person = session.scalar(select(PersonRecord).where(PersonRecord.name == rule.person_name))
            if person is None:
                raise ValueError(f"Unknown person {rule.person_name!r}.")
            record = session.scalar(select(RuleRecord).where(RuleRecord.person_id == person.id))
            if record is None:
                record = RuleRecord(
                    person_id=person.id,
                    minimum=rule.minimum,
                    maximum=rule.maximum,
                )
                session.add(record)
            else:
                record.minimum = rule.minimum
                record.maximum = rule.maximum
        return rule

    def list_rules(self) -> list[AssignmentRule]:
        with self._sessions() as session:
            rows = session.execute(
                select(RuleRecord, PersonRecord.name)
                .join(PersonRecord, PersonRecord.id == RuleRecord.person_id)
                .order_by(PersonRecord.name)
            ).all()
        return [
            AssignmentRule(name, row.minimum, row.maximum)
            for row, name in rows
        ]

    def set_chore_assignment_rule(
        self,
        person_name: str,
        chore_name: str,
        weekday: Weekday,
        is_assigned: bool,
    ) -> ChoreAssignmentRule:
        rule = ChoreAssignmentRule(person_name, chore_name, weekday, is_assigned)
        with self._sessions.begin() as session:
            person = session.scalar(select(PersonRecord).where(PersonRecord.name == rule.person_name))
            chore = session.scalar(select(ChoreRecord).where(ChoreRecord.name == rule.chore_name))
            if person is None:
                raise ValueError(f"Unknown person {rule.person_name!r}.")
            if chore is None:
                raise ValueError(f"Unknown chore {rule.chore_name!r}.")
            if rule.is_assigned:
                conflicting_assignment = session.scalar(
                    select(ChoreAssignmentRuleRecord).where(
                        ChoreAssignmentRuleRecord.chore_id == chore.id,
                        ChoreAssignmentRuleRecord.weekday == rule.weekday.value,
                        ChoreAssignmentRuleRecord.is_assigned.is_(True),
                        ChoreAssignmentRuleRecord.person_id != person.id,
                    )
                )
                if conflicting_assignment is not None:
                    raise ValueError(
                        f"Another person is already required to do {rule.chore_name} on "
                        f"{rule.weekday.value}."
                    )
            record = session.scalar(
                select(ChoreAssignmentRuleRecord).where(
                    ChoreAssignmentRuleRecord.person_id == person.id,
                    ChoreAssignmentRuleRecord.chore_id == chore.id,
                    ChoreAssignmentRuleRecord.weekday == rule.weekday.value,
                )
            )
            if record is None:
                record = ChoreAssignmentRuleRecord(
                    person_id=person.id,
                    chore_id=chore.id,
                    weekday=rule.weekday.value,
                    is_assigned=rule.is_assigned,
                )
                session.add(record)
                session.flush()
            else:
                record.is_assigned = rule.is_assigned
            rule = ChoreAssignmentRule(
                rule.person_name, rule.chore_name, rule.weekday, rule.is_assigned, record.id
            )
        return rule

    def list_chore_assignment_rules(self) -> list[ChoreAssignmentRule]:
        with self._sessions() as session:
            rows = session.execute(
                select(ChoreAssignmentRuleRecord, PersonRecord.name, ChoreRecord.name)
                .join(PersonRecord, PersonRecord.id == ChoreAssignmentRuleRecord.person_id)
                .join(ChoreRecord, ChoreRecord.id == ChoreAssignmentRuleRecord.chore_id)
                .order_by(
                    ChoreAssignmentRuleRecord.weekday,
                    ChoreRecord.name,
                    PersonRecord.name,
                )
            ).all()
        return [
            ChoreAssignmentRule(
                person_name,
                chore_name,
                Weekday.parse(record.weekday),
                record.is_assigned,
                record.id,
            )
            for record, person_name, chore_name in rows
        ]

    def delete_chore_assignment_rule(self, rule_id: int) -> None:
        with self._sessions.begin() as session:
            record = session.get(ChoreAssignmentRuleRecord, rule_id)
            if record is None:
                raise ValueError(f"No chore assignment rule with id {rule_id}.")
            session.delete(record)

    def generate_and_save(self, seed: int | None = None) -> Schedule:
        from chore_chart.scheduler import generate_schedule

        schedule = generate_schedule(
            people=self.list_people(),
            chores=self.list_chores(),
            rules=self.list_rules(),
            assignment_rules=self.list_chore_assignment_rules(),
            history=self.recent_schedules(4),
            seed=seed,
        )
        return self.save_schedule(schedule)

    def save_schedule(self, schedule: Schedule) -> Schedule:
        created_at = datetime.now(timezone.utc)
        with self._sessions.begin() as session:
            record = ScheduleRecord(created_at=created_at, seed=schedule.seed)
            session.add(record)
            session.flush()
            schedule_id = record.id
            session.add_all(
                AssignmentRecord(
                    schedule_id=schedule_id,
                    chore_id=assignment.occurrence.chore_id,
                    person_id=assignment.person_id
                    or session.scalar(
                        select(PersonRecord.id).where(PersonRecord.name == assignment.person_name)
                    ),
                    section_id=assignment.occurrence.section_id,
                    weekday=assignment.occurrence.weekday.value,
                    start_minute=(
                        assignment.occurrence.time_range.start_minute
                        if assignment.occurrence.time_range
                        else None
                    ),
                    end_minute=(
                        assignment.occurrence.time_range.end_minute
                        if assignment.occurrence.time_range
                        else None
                    ),
                )
                for assignment in schedule.assignments
            )
        return self.get_schedule(schedule_id)

    def recent_schedules(self, limit: int = 4) -> list[Schedule]:
        with self._sessions() as session:
            schedule_ids = session.scalars(
                select(ScheduleRecord.id).order_by(ScheduleRecord.id.desc()).limit(limit)
            ).all()
        return [self.get_schedule(schedule_id) for schedule_id in reversed(schedule_ids)]

    def get_schedule(self, schedule_id: int) -> Schedule:
        with self._sessions() as session:
            row = session.get(ScheduleRecord, schedule_id)
            if row is None:
                raise ValueError(f"No saved schedule with id {schedule_id}.")
            assignments = session.execute(
                select(AssignmentRecord, ChoreRecord, PersonRecord, SectionRecord)
                .join(ChoreRecord, AssignmentRecord.chore_id == ChoreRecord.id)
                .join(PersonRecord, AssignmentRecord.person_id == PersonRecord.id)
                .join(SectionRecord, AssignmentRecord.section_id == SectionRecord.id)
                .where(AssignmentRecord.schedule_id == schedule_id)
                .order_by(
                    SectionRecord.order_index,
                    ChoreRecord.order_index,
                    AssignmentRecord.id,
                )
            ).all()
        return Schedule(
            schedule_id=row.id,
            seed=row.seed,
            created_at=row.created_at,
            assignments=tuple(
                Assignment(
                    occurrence=ChoreOccurrence(
                        chore_name=chore.name,
                        weekday=Weekday.parse(assignment.weekday),
                        section_name=section.name,
                        occurrence_id=None,
                        chore_id=chore.id,
                        chore_label=chore.label or chore.name,
                        time_range=(
                            TimeRange(assignment.start_minute, assignment.end_minute)
                            if assignment.start_minute is not None and assignment.end_minute is not None
                            else None
                        ),
                        section_id=section.id,
                        section_order=section.order_index,
                        chore_order=chore.order_index,
                    ),
                    person_name=person.name,
                    person_id=person.id,
                    assignment_id=assignment.id,
                )
                for assignment, chore, person, section in assignments
            ),
        )

    def update_schedule_assignments(
        self,
        schedule_id: int,
        assignments: Sequence[tuple[int, int]],
    ) -> Schedule:
        assignment_ids = [assignment_id for assignment_id, _ in assignments]
        if len(assignment_ids) != len(set(assignment_ids)):
            raise ValueError("Each schedule assignment can only be updated once.")
        with self._sessions.begin() as session:
            if session.get(ScheduleRecord, schedule_id) is None:
                raise ValueError(f"No saved schedule with id {schedule_id}.")
            records = session.scalars(
                select(AssignmentRecord).where(AssignmentRecord.schedule_id == schedule_id)
            ).all()
            records_by_id = {record.id: record for record in records}
            if set(assignment_ids) != records_by_id.keys():
                raise ValueError("Assignments must include every assignment in this schedule.")
            person_ids = {person_id for _, person_id in assignments}
            existing_person_ids = set(
                session.scalars(select(PersonRecord.id).where(PersonRecord.id.in_(person_ids))).all()
            ) if person_ids else set()
            if person_ids != existing_person_ids:
                raise ValueError("One or more selected people do not exist.")
            assignment_persons = dict(assignments)
            assignment_rules = session.scalars(select(ChoreAssignmentRuleRecord)).all()
            for record in records:
                new_person_id = assignment_persons[record.id]
                for rule in assignment_rules:
                    if rule.chore_id != record.chore_id or rule.weekday != record.weekday:
                        continue
                    if rule.is_assigned and new_person_id != rule.person_id:
                        raise ValueError(
                            f"This assignment is fixed to the required person for {record.weekday}."
                        )
                    if not rule.is_assigned and new_person_id == rule.person_id:
                        raise ValueError(
                            f"This person may not be assigned this chore on {record.weekday}."
                        )
            for assignment_id, person_id in assignments:
                records_by_id[assignment_id].person_id = person_id
        return self.get_schedule(schedule_id)

    def list_schedules(self, limit: int = 20) -> list[Schedule]:
        with self._sessions() as session:
            schedule_ids = session.scalars(
                select(ScheduleRecord.id).order_by(ScheduleRecord.id.desc()).limit(limit)
            ).all()
        return [self.get_schedule(schedule_id) for schedule_id in schedule_ids]