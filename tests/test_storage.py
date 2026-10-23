import sqlite3

import pytest

from chore_chart.domain import AvailabilitySlot, ChoreOccurrence, TimeRange, Weekday
from chore_chart.storage import SQLiteRepository


@pytest.fixture
def repository():
    instance = SQLiteRepository(":memory:")
    yield instance
    instance.close()


def test_people_and_availability_round_trip(repository):
    repository.add_person("Alex")
    repository.set_availability(
        "Alex",
        [
            AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("08:00-12:00")),
            AvailabilitySlot(Weekday.SATURDAY, TimeRange.parse("13:00-18:00")),
        ],
    )

    person = repository.list_people()[0]
    assert person.name == "Alex"
    assert person.availability == {
        AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("08:00-12:00")),
        AvailabilitySlot(Weekday.SATURDAY, TimeRange.parse("13:00-18:00")),
    }


def test_setting_availability_replaces_previous_slots(repository):
    repository.add_person("Alex")
    repository.set_availability("Alex", [AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("08:00-12:00"))])
    repository.set_availability("Alex", [AvailabilitySlot(Weekday.TUESDAY, TimeRange.parse("17:00-20:00"))])

    assert repository.list_people()[0].availability == {
        AvailabilitySlot(Weekday.TUESDAY, TimeRange.parse("17:00-20:00"))
    }


def test_clear_availability_removes_all_slots(repository):
    repository.add_person("Alex")
    repository.set_availability(
        "Alex",
        [
            AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("08:00-12:00")),
            AvailabilitySlot(Weekday.SATURDAY, TimeRange.parse("13:00-18:00")),
        ],
    )

    repository.clear_availability("Alex")

    assert repository.list_people()[0].availability == set()


def test_new_person_starts_with_all_day_availability(repository):
    person = repository.add_person("Alex")

    assert person.availability == {
        AvailabilitySlot(day, TimeRange(0, 1440)) for day in Weekday
    }


def test_duplicate_person_and_unknown_availability_person_are_rejected(repository):
    repository.add_person("Alex")

    with pytest.raises(ValueError, match="already exists"):
        repository.add_person("Alex")
    with pytest.raises(ValueError, match="Unknown person"):
        repository.set_availability("Sam", [])


def test_chores_and_occurrences_round_trip(repository):
    occurrences = [
        ChoreOccurrence("Dishes", Weekday.MONDAY, "Dinner", time_range=TimeRange.parse("16:00-17:30")),
        ChoreOccurrence("Dishes", Weekday.FRIDAY, "Morning", time_range=TimeRange.parse("08:00-09:00")),
    ]

    added = repository.add_chore("Dishes", occurrences)
    listed = repository.list_chores()[0]

    assert added.chore_id is not None
    assert listed.name == "Dishes"
    assert [(item.weekday, item.time_label, item.time_range) for item in listed.occurrences] == [
        (Weekday.MONDAY, "Dinner", TimeRange.parse("16:00-17:30")),
        (Weekday.FRIDAY, "Morning", TimeRange.parse("08:00-09:00")),
    ]
    assert all(item.chore_name == "Dishes" for item in listed.occurrences)


def test_adding_existing_chore_is_additive_and_duplicate_slots_in_one_request_are_rejected(repository):
    occurrence = ChoreOccurrence("Dishes", Weekday.MONDAY, "Morning")
    repository.add_chore("Dishes", [occurrence])
    repository.add_chore("Dishes", [occurrence, ChoreOccurrence("Dishes", Weekday.TUESDAY, "Morning")])

    assert len(repository.list_chores()[0].occurrences) == 2
    with pytest.raises(ValueError, match="duplicate occurrences"):
        repository.add_chore("Laundry", [
            ChoreOccurrence("Laundry", Weekday.MONDAY, "Morning"),
            ChoreOccurrence("Laundry", Weekday.MONDAY, "morning"),
        ])


def test_same_chore_group_can_have_distinct_time_ranges_on_same_day(repository):
    repository.add_chore(
        "Dinner",
        [ChoreOccurrence("Dinner", Weekday.MONDAY, "Meal", time_range=TimeRange.parse("16:00-17:00"))],
    )
    repository.add_chore(
        "Dinner",
        [ChoreOccurrence("Dinner", Weekday.MONDAY, "Meal", time_range=TimeRange.parse("18:00-19:00"))],
    )

    occurrences = repository.list_chores()[0].occurrences
    assert [item.time_range for item in occurrences] == [
        TimeRange.parse("16:00-17:00"),
        TimeRange.parse("18:00-19:00"),
    ]


def test_rules_round_trip_and_update(repository):
    repository.add_person("Alex")
    repository.set_rule("Alex", minimum=1, maximum=3)
    repository.set_rule("Alex", minimum=2, maximum=None)

    assert repository.list_rules()[0].minimum == 2
    assert repository.list_rules()[0].maximum is None

    with pytest.raises(ValueError, match="Unknown person"):
        repository.set_rule("Sam", 0, 1)


def test_schedule_generation_persists_history_and_can_be_exported_again(repository):
    repository.add_person("Alex")
    repository.add_person("Sam")
    repository.set_availability("Alex", [AvailabilitySlot(Weekday.MONDAY, TimeRange(0, 1440))])
    repository.set_availability("Sam", [AvailabilitySlot(Weekday.MONDAY, TimeRange(0, 1440))])
    repository.add_chore(
        "Dishes",
        [ChoreOccurrence("Dishes", Weekday.MONDAY, "Morning", time_range=TimeRange.parse("09:00-10:00"))],
    )

    first = repository.generate_and_save(seed=10)
    restored = repository.get_schedule(first.schedule_id)
    second = repository.generate_and_save(seed=11)

    assert [
        (
            item.occurrence.chore_name,
            item.occurrence.weekday,
            item.occurrence.time_label,
            item.occurrence.time_range,
            item.person_name,
        )
        for item in restored.assignments
    ] == [
        (
            item.occurrence.chore_name,
            item.occurrence.weekday,
            item.occurrence.time_label,
            item.occurrence.time_range,
            item.person_name,
        )
        for item in first.assignments
    ]
    assert second.assignments[0].person_name != first.assignments[0].person_name
    assert [item.schedule_id for item in repository.list_schedules()] == [second.schedule_id, first.schedule_id]
    assert repository.recent_schedules(1)[0].schedule_id == second.schedule_id


def test_get_schedule_reports_unknown_id(repository):
    with pytest.raises(ValueError, match="No saved schedule with id 1"):
        repository.get_schedule(1)


def test_removing_occurrences_keeps_other_days_and_deletes_empty_chore(repository):
    repository.add_chore(
        "Trash",
        [
            ChoreOccurrence("Trash", Weekday.MONDAY, "Anytime"),
            ChoreOccurrence("Trash", Weekday.TUESDAY, "Anytime"),
        ],
    )

    removed = repository.remove_chore(
        "Trash",
        [ChoreOccurrence("Trash", Weekday.MONDAY, "anytime")],
    )

    assert removed == 1
    assert [item.weekday for item in repository.list_chores()[0].occurrences] == [Weekday.TUESDAY]
    assert repository.remove_chore("Trash") == 1
    assert repository.list_chores() == []


def test_distinct_chores_can_share_the_same_display_label(repository):
    repository.add_chore(
        "trash_alex",
        [ChoreOccurrence("trash_alex", Weekday.MONDAY, "Anytime")],
        label="Trash",
    )
    repository.add_chore(
        "trash_sam",
        [ChoreOccurrence("trash_sam", Weekday.MONDAY, "Anytime")],
        label="Trash",
    )

    chores = repository.list_chores()
    assert [(chore.name, chore.label) for chore in chores] == [
        ("trash_alex", "Trash"),
        ("trash_sam", "Trash"),
    ]


def test_existing_database_is_migrated_and_labels_are_backfilled(tmp_path):
    database_path = tmp_path / "legacy.sqlite3"
    repository = SQLiteRepository(database_path)
    repository.add_person("Alex")
    repository.set_availability("Alex", [AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("08:00-12:00"))])
    repository.add_chore(
        "Dishes",
        [ChoreOccurrence("Dishes", Weekday.MONDAY, "Morning", time_range=TimeRange.parse("09:00-10:00"))],
    )
    saved = repository.generate_and_save(seed=4)
    repository.close()

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("ALTER TABLE chores DROP COLUMN label")
        connection.execute("ALTER TABLE schedule_assignments DROP COLUMN chore_label")
        connection.execute("ALTER TABLE availability DROP COLUMN start_minute")
        connection.execute("ALTER TABLE availability DROP COLUMN end_minute")
        connection.execute("ALTER TABLE schedule_assignments DROP COLUMN start_minute")
        connection.execute("ALTER TABLE schedule_assignments DROP COLUMN end_minute")
        connection.execute("ALTER TABLE occurrences RENAME TO occurrences_with_ranges")
        connection.execute(
            "CREATE TABLE occurrences ("
            "id INTEGER PRIMARY KEY, chore_id INTEGER NOT NULL REFERENCES chores(id) ON DELETE CASCADE, "
            "weekday VARCHAR(9) NOT NULL, time_label VARCHAR(80) NOT NULL, "
            "UNIQUE (chore_id, weekday, time_label))"
        )
        connection.execute(
            "INSERT INTO occurrences (id, chore_id, weekday, time_label) "
            "SELECT id, chore_id, weekday, time_label FROM occurrences_with_ranges"
        )
        connection.execute("DROP TABLE occurrences_with_ranges")
        connection.commit()
    finally:
        connection.close()

    migrated = SQLiteRepository(database_path)
    try:
        assert migrated.list_chores()[0].label == "Dishes"
        alex = migrated.list_people()[0]
        assert alex.availability == {
            AvailabilitySlot(day, TimeRange(0, 1440)) for day in Weekday
        }
        restored = migrated.get_schedule(saved.schedule_id)
        assert restored.assignments[0].occurrence.chore_label == "Dishes"
        assert restored.assignments[0].occurrence.time_range is None
    finally:
        migrated.close()