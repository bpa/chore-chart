from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, create_engine, delete, event, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from chore_chart.domain import (
    Assignment,
    AssignmentRule,
    AvailabilitySlot,
    Chore,
    ChoreOccurrence,
    Person,
    Schedule,
    TimeRange,
    Weekday,
)


class Base(DeclarativeBase):
    pass


class PersonRecord(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class AvailabilityRecord(Base):
    __tablename__ = "availability"
    __table_args__ = (
        UniqueConstraint("person_id", "weekday", "time_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    weekday: Mapped[str] = mapped_column(String(9))
    time_label: Mapped[str] = mapped_column(String(80))
    start_minute: Mapped[int] = mapped_column(Integer, default=0)
    end_minute: Mapped[int] = mapped_column(Integer, default=1440)


class ChoreRecord(Base):
    __tablename__ = "chores"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    label: Mapped[str] = mapped_column(String(160), default="")


class OccurrenceRecord(Base):
    __tablename__ = "occurrences"
    __table_args__ = (
        UniqueConstraint("chore_id", "weekday", "time_label", "start_minute", "end_minute"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chore_id: Mapped[int] = mapped_column(ForeignKey("chores.id", ondelete="CASCADE"))
    weekday: Mapped[str] = mapped_column(String(9))
    time_label: Mapped[str] = mapped_column(String(80))
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RuleRecord(Base):
    __tablename__ = "assignment_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), unique=True)
    minimum: Mapped[int] = mapped_column(default=0)
    maximum: Mapped[int | None]


class ScheduleRecord(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    seed: Mapped[int | None]


class AssignmentRecord(Base):
    __tablename__ = "schedule_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    chore_name: Mapped[str] = mapped_column(String(160))
    chore_label: Mapped[str] = mapped_column(String(160), default="")
    weekday: Mapped[str] = mapped_column(String(9))
    time_label: Mapped[str] = mapped_column(String(80))
    start_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    person_name: Mapped[str] = mapped_column(String(120))


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
        Base.metadata.create_all(self._engine)
        self._upgrade_schema()

    def _upgrade_schema(self) -> None:
        with self._engine.begin() as connection:
            chore_columns = {column["name"] for column in inspect(connection).get_columns("chores")}
            if "label" not in chore_columns:
                connection.exec_driver_sql("ALTER TABLE chores ADD COLUMN label VARCHAR(160)")
            connection.exec_driver_sql("UPDATE chores SET label = name WHERE label IS NULL OR label = ''")

            assignment_columns = {
                column["name"]
                for column in inspect(connection).get_columns("schedule_assignments")
            }
            if "chore_label" not in assignment_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE schedule_assignments ADD COLUMN chore_label VARCHAR(160)"
                )
            connection.exec_driver_sql(
                "UPDATE schedule_assignments SET chore_label = chore_name "
                "WHERE chore_label IS NULL OR chore_label = ''"
            )
            for table, columns in (
                ("availability", {"start_minute": "INTEGER NOT NULL DEFAULT 0", "end_minute": "INTEGER NOT NULL DEFAULT 1440"}),
                ("occurrences", {"start_minute": "INTEGER", "end_minute": "INTEGER"}),
                ("schedule_assignments", {"start_minute": "INTEGER", "end_minute": "INTEGER"}),
            ):
                existing_columns = {
                    column["name"] for column in inspect(connection).get_columns(table)
                }
                availability_ranges_missing = table == "availability" and not {
                    "start_minute", "end_minute"
                }.issubset(existing_columns)
                for name, definition in columns.items():
                    if name not in existing_columns:
                        connection.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
                if availability_ranges_missing:
                    connection.exec_driver_sql("DELETE FROM availability")
                    person_ids = connection.exec_driver_sql("SELECT id FROM people").scalars().all()
                    for person_id in person_ids:
                        for day in Weekday:
                            connection.exec_driver_sql(
                                "INSERT INTO availability "
                                "(person_id, weekday, time_label, start_minute, end_minute) "
                                "VALUES (?, ?, ?, 0, 1440)",
                                (person_id, day.value, "00:00-24:00"),
                            )
            unique_constraints = inspect(connection).get_unique_constraints("occurrences")
            if any(
                set(constraint["column_names"]) == {"chore_id", "weekday", "time_label"}
                for constraint in unique_constraints
            ):
                connection.exec_driver_sql(
                    "CREATE TABLE occurrences_new ("
                    "id INTEGER PRIMARY KEY, "
                    "chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE, "
                    "weekday VARCHAR(9) NOT NULL, "
                    "time_label VARCHAR(80) NOT NULL, "
                    "start_minute INTEGER, end_minute INTEGER, "
                    "CONSTRAINT uq_occurrence_range UNIQUE "
                    "(chore_id, weekday, time_label, start_minute, end_minute))"
                )
                connection.exec_driver_sql(
                    "INSERT INTO occurrences_new "
                    "(id, chore_id, weekday, time_label, start_minute, end_minute) "
                    "SELECT id, chore_id, weekday, time_label, start_minute, end_minute "
                    "FROM occurrences"
                )
                connection.exec_driver_sql("DROP TABLE occurrences")
                connection.exec_driver_sql("ALTER TABLE occurrences_new RENAME TO occurrences")

    @staticmethod
    def _enable_foreign_keys(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def set_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def close(self) -> None:
        self._engine.dispose()

    def add_person(self, name: str) -> Person:
        person = Person(name)
        with self._sessions.begin() as session:
            if session.scalar(select(PersonRecord).where(PersonRecord.name == person.name)):
                raise ValueError(f"Person {person.name!r} already exists.")
            record = PersonRecord(name=person.name)
            session.add(record)
            session.flush()
            person_id = record.id
            full_day = TimeRange(0, 1440)
            session.add_all(
                AvailabilityRecord(
                    person_id=person_id,
                    weekday=day.value,
                    time_label=str(full_day),
                    start_minute=full_day.start_minute,
                    end_minute=full_day.end_minute,
                )
                for day in Weekday
            )
        return Person(
            name=person.name,
            person_id=person_id,
            availability=frozenset(AvailabilitySlot(day, full_day) for day in Weekday),
        )

    def list_people(self) -> list[Person]:
        with self._sessions() as session:
            records = session.scalars(select(PersonRecord).order_by(PersonRecord.name)).all()
            availability_rows = session.scalars(select(AvailabilityRecord)).all()
        slots_by_person: dict[int, set[AvailabilitySlot]] = {}
        for row in availability_rows:
            slots_by_person.setdefault(row.person_id, set()).add(
                AvailabilitySlot(
                    Weekday.parse(row.weekday),
                    TimeRange(row.start_minute, row.end_minute),
                )
            )
        return [
            Person(
                name=record.name,
                person_id=record.id,
                availability=frozenset(slots_by_person.get(record.id, set())),
            )
            for record in records
        ]

    def set_availability(self, person_name: str, slots: Iterable[AvailabilitySlot]) -> None:
        normalized_slots = set(slots)
        with self._sessions.begin() as session:
            person = session.scalar(select(PersonRecord).where(PersonRecord.name == person_name.strip()))
            if person is None:
                raise ValueError(f"Unknown person {person_name!r}.")
            session.execute(delete(AvailabilityRecord).where(AvailabilityRecord.person_id == person.id))
            session.add_all(
                AvailabilityRecord(
                    person_id=person.id,
                    weekday=slot.weekday.value,
                    time_label=str(slot.time_range),
                    start_minute=slot.time_range.start_minute,
                    end_minute=slot.time_range.end_minute,
                )
                for slot in sorted(
                    normalized_slots,
                    key=lambda item: (
                        item.weekday.order,
                        item.time_range.start_minute,
                        item.time_range.end_minute,
                    ),
                )
            )

    def add_chore(
        self,
        name: str,
        occurrences: Sequence[ChoreOccurrence],
        label: str | None = None,
    ) -> Chore:
        chore_name = name.strip()
        if not chore_name:
            raise ValueError("Chore name cannot be empty.")
        if not occurrences:
            raise ValueError("A chore must have at least one scheduled occurrence.")
        chore_label = label.strip() if label is not None else chore_name
        if not chore_label:
            raise ValueError("Chore label cannot be empty.")
        normalized = [
            ChoreOccurrence(
                chore_name=chore_name,
                weekday=occurrence.weekday,
                time_label=occurrence.time_label,
                chore_label=chore_label,
                time_range=occurrence.time_range,
            )
            for occurrence in occurrences
        ]
        keys = [
            (item.weekday, item.time_label.casefold(), item.time_range)
            for item in normalized
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("A chore cannot have duplicate occurrences at the same weekday and time.")

        with self._sessions.begin() as session:
            record = session.scalar(select(ChoreRecord).where(ChoreRecord.name == chore_name))
            if record is None:
                record = ChoreRecord(name=chore_name, label=chore_label)
                session.add(record)
                session.flush()
            elif label is None:
                chore_label = record.label or chore_name
            else:
                record.label = chore_label

            existing = session.scalars(
                select(OccurrenceRecord).where(OccurrenceRecord.chore_id == record.id)
            ).all()
            existing_keys = {
                (
                    item.weekday,
                    item.time_label.casefold(),
                    TimeRange(item.start_minute, item.end_minute)
                    if item.start_minute is not None and item.end_minute is not None
                    else None,
                )
                for item in existing
            }
            new_occurrences = [
                item for item in normalized
                if (item.weekday.value, item.time_label.casefold(), item.time_range) not in existing_keys
            ]
            session.add_all(
                OccurrenceRecord(
                    chore_id=record.id,
                    weekday=occurrence.weekday.value,
                    time_label=occurrence.time_label,
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
                .order_by(OccurrenceRecord.id)
            ).all()
            record_id = record.id

        return Chore(
            name=chore_name,
            label=chore_label,
            chore_id=record_id,
            occurrences=tuple(
                ChoreOccurrence(
                    chore_name=chore_name,
                    weekday=Weekday.parse(item.weekday),
                    time_label=item.time_label,
                    occurrence_id=item.id,
                    chore_id=record_id,
                    chore_label=chore_label,
                    time_range=(
                        TimeRange(item.start_minute, item.end_minute)
                        if item.start_minute is not None and item.end_minute is not None
                        else None
                    ),
                )
                for item in sorted(
                    all_occurrences,
                    key=lambda item: (
                        Weekday.parse(item.weekday).order,
                        item.start_minute if item.start_minute is not None else -1,
                        item.time_label.casefold(),
                    ),
                )
            ),
        )

    def remove_chore(
        self,
        name: str,
        occurrences: Sequence[ChoreOccurrence] | None = None,
    ) -> int:
        chore_name = name.strip()
        with self._sessions.begin() as session:
            chore = session.scalar(select(ChoreRecord).where(ChoreRecord.name == chore_name))
            if chore is None:
                raise ValueError(f"Unknown chore {chore_name!r}.")
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
                    func.lower(OccurrenceRecord.time_label) == occurrence.time_label.casefold(),
                )
                if occurrence.time_range is not None:
                    statement = statement.where(
                        OccurrenceRecord.start_minute == occurrence.time_range.start_minute,
                        OccurrenceRecord.end_minute == occurrence.time_range.end_minute,
                    )
                result = session.execute(statement)
                removed += result.rowcount or 0
            if not removed:
                raise ValueError(f"No matching occurrence found for chore {chore_name!r}.")

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
            chore_records = session.scalars(select(ChoreRecord).order_by(ChoreRecord.name)).all()
            occurrence_records = session.scalars(select(OccurrenceRecord)).all()
        by_chore: dict[int, list[OccurrenceRecord]] = {}
        for row in occurrence_records:
            by_chore.setdefault(row.chore_id, []).append(row)
        return [
            Chore(
                name=record.name,
                label=record.label or record.name,
                chore_id=record.id,
                occurrences=tuple(
                    ChoreOccurrence(
                        chore_name=record.name,
                        weekday=Weekday.parse(item.weekday),
                        time_label=item.time_label,
                        occurrence_id=item.id,
                        chore_id=record.id,
                        chore_label=record.label or record.name,
                        time_range=(
                            TimeRange(item.start_minute, item.end_minute)
                            if item.start_minute is not None and item.end_minute is not None
                            else None
                        ),
                    )
                    for item in sorted(
                        by_chore.get(record.id, []),
                        key=lambda item: (
                            Weekday.parse(item.weekday).order,
                            item.start_minute if item.start_minute is not None else -1,
                            item.time_label.casefold(),
                        ),
                    )
                ),
            )
            for record in chore_records
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

    def generate_and_save(self, seed: int | None = None) -> Schedule:
        from chore_chart.scheduler import generate_schedule

        schedule = generate_schedule(
            people=self.list_people(),
            chores=self.list_chores(),
            rules=self.list_rules(),
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
                    chore_name=assignment.occurrence.chore_name,
                    chore_label=assignment.occurrence.chore_label or assignment.occurrence.chore_name,
                    weekday=assignment.occurrence.weekday.value,
                    time_label=assignment.occurrence.time_label,
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
                    person_name=assignment.person_name,
                )
                for assignment in schedule.assignments
            )
        return Schedule(
            assignments=schedule.assignments,
            schedule_id=schedule_id,
            seed=schedule.seed,
            created_at=created_at,
        )

    def recent_schedules(self, limit: int = 4) -> list[Schedule]:
        with self._sessions() as session:
            schedule_rows = session.scalars(
                select(ScheduleRecord).order_by(ScheduleRecord.id.desc()).limit(limit)
            ).all()
            result = []
            for row in reversed(schedule_rows):
                assignments = session.scalars(
                    select(AssignmentRecord)
                    .where(AssignmentRecord.schedule_id == row.id)
                    .order_by(AssignmentRecord.id)
                ).all()
                result.append(
                    Schedule(
                        schedule_id=row.id,
                        seed=row.seed,
                        created_at=row.created_at,
                        assignments=tuple(
                            Assignment(
                                occurrence=ChoreOccurrence(
                                    chore_name=assignment.chore_name,
                                    weekday=Weekday.parse(assignment.weekday),
                                    time_label=assignment.time_label,
                                    chore_label=assignment.chore_label or assignment.chore_name,
                                    time_range=(
                                        TimeRange(assignment.start_minute, assignment.end_minute)
                                        if assignment.start_minute is not None and assignment.end_minute is not None
                                        else None
                                    ),
                                ),
                                person_name=assignment.person_name,
                            )
                            for assignment in assignments
                        ),
                    )
                )
        return result

    def get_schedule(self, schedule_id: int) -> Schedule:
        with self._sessions() as session:
            row = session.get(ScheduleRecord, schedule_id)
            if row is None:
                raise ValueError(f"No saved schedule with id {schedule_id}.")
            assignments = session.scalars(
                select(AssignmentRecord)
                .where(AssignmentRecord.schedule_id == schedule_id)
                .order_by(AssignmentRecord.id)
            ).all()
        return Schedule(
            schedule_id=row.id,
            seed=row.seed,
            created_at=row.created_at,
            assignments=tuple(
                Assignment(
                    occurrence=ChoreOccurrence(
                        chore_name=assignment.chore_name,
                        weekday=Weekday.parse(assignment.weekday),
                        time_label=assignment.time_label,
                        chore_label=assignment.chore_label or assignment.chore_name,
                        time_range=(
                            TimeRange(assignment.start_minute, assignment.end_minute)
                            if assignment.start_minute is not None and assignment.end_minute is not None
                            else None
                        ),
                    ),
                    person_name=assignment.person_name,
                )
                for assignment in assignments
            ),
        )

    def list_schedules(self, limit: int = 20) -> list[Schedule]:
        with self._sessions() as session:
            schedule_ids = session.scalars(
                select(ScheduleRecord.id).order_by(ScheduleRecord.id.desc()).limit(limit)
            ).all()
        return [self.get_schedule(schedule_id) for schedule_id in schedule_ids]