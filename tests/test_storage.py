import sqlite3

import pytest

from chore_chart.domain import AvailabilitySlot, ChoreOccurrence, TimeRange, Weekday
from chore_chart.storage import AssignmentRecord, SQLiteRepository


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
        ChoreOccurrence("Dishes", Weekday.FRIDAY, "Dinner", time_range=TimeRange.parse("08:00-09:00")),
    ]

    added = repository.add_chore("Dishes", occurrences)
    listed = repository.list_chores()[0]

    assert added.chore_id is not None
    assert listed.name == "Dishes"
    assert [(item.weekday, item.section_name, item.time_range) for item in listed.occurrences] == [
        (Weekday.MONDAY, "Dinner", TimeRange.parse("16:00-17:30")),
        (Weekday.FRIDAY, "Dinner", TimeRange.parse("08:00-09:00")),
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
            item.occurrence.section_name,
            item.occurrence.time_range,
            item.person_name,
        )
        for item in restored.assignments
    ] == [
        (
            item.occurrence.chore_name,
            item.occurrence.weekday,
            item.occurrence.section_name,
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

    chore = repository.list_chores()[0]
    removed = repository.remove_chore(
        chore.chore_id,
        [ChoreOccurrence("Trash", Weekday.MONDAY, "anytime")],
    )

    assert removed == 1
    assert [item.weekday for item in repository.list_chores()[0].occurrences] == [Weekday.TUESDAY]
    assert repository.remove_chore(repository.list_chores()[0].chore_id) == 1
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


def test_add_chore_stores_unquoted_name_and_label(repository):
    repository.add_chore(
        "'Dishes'",
        [ChoreOccurrence("'Dishes'", Weekday.MONDAY, "Morning")],
        label='"Kitchen"',
    )

    chore = repository.list_chores()[0]
    assert chore.name == "Dishes"
    assert chore.label == "Kitchen"


def test_update_chore_replaces_occurrences_and_updates_label(repository):
    repository.add_chore(
        "Dishes",
        [
            ChoreOccurrence("Dishes", Weekday.MONDAY, "Anytime"),
            ChoreOccurrence("Dishes", Weekday.TUESDAY, "Anytime"),
        ],
        label="Kitchen",
    )

    existing_chore = repository.list_chores()[0]
    updated = repository.update_chore(
        existing_chore.chore_id,
        [
            ChoreOccurrence(
                "Dishes",
                Weekday.WEDNESDAY,
                "Evening",
                time_range=TimeRange.parse("18:00-20:00"),
            )
        ],
        label="Dinner dishes",
        section_id=existing_chore.section_id,
        section_name=existing_chore.section_name,
    )

    assert updated.label == "Dinner dishes"
    assert [(item.weekday, item.section_name, item.time_range) for item in updated.occurrences] == [
        (Weekday.WEDNESDAY, "Anytime", TimeRange.parse("18:00-20:00"))
    ]
    assert repository.list_chores()[0] == updated


def test_sections_and_chore_order_are_persisted(repository):
    evening = repository.add_section("Evening")
    anytime = repository.add_section("Anytime")
    repository.update_section(evening.section_id, "Evening", 1)
    repository.update_section(anytime.section_id, "Anytime", 0)
    repository.add_chore(
        "Dishes",
        [ChoreOccurrence("Dishes", Weekday.MONDAY, "Evening")],
        section_id=evening.section_id,
        order_index=0,
    )
    repository.add_chore(
        "Sweep",
        [ChoreOccurrence("Sweep", Weekday.MONDAY, "Anytime")],
        section_id=anytime.section_id,
        order_index=0,
    )
    dust = repository.add_chore(
        "Dust",
        [ChoreOccurrence("Dust", Weekday.TUESDAY, "Anytime")],
        section_id=anytime.section_id,
        order_index=1,
    )
    repository.update_chore(
        dust.chore_id,
        [ChoreOccurrence("Dust", Weekday.TUESDAY, "Anytime")],
        label="Dust",
        section_id=anytime.section_id,
        order_index=0,
    )
    repository.add_chore(
        "Mop",
        [ChoreOccurrence("Mop", Weekday.WEDNESDAY, "Anytime")],
        section_id=anytime.section_id,
        order_index=1,
    )
    repository.update_section(evening.section_id, "Evening", 0)

    assert [(chore.section_name, chore.name, chore.order_index) for chore in repository.list_chores()] == [
        ("Evening", "Dishes", 0),
        ("Anytime", "Dust", 0),
        ("Anytime", "Mop", 1),
        ("Anytime", "Sweep", 2),
    ]


def test_saved_assignments_use_ids_and_follow_section_renames(repository):
    repository.add_person("Alex")
    section = repository.add_section("Kitchen")
    chore = repository.add_chore(
        "Dishes",
        [ChoreOccurrence("Dishes", Weekday.MONDAY, "Kitchen")],
        section_id=section.section_id,
        order_index=2,
    )
    saved = repository.generate_and_save(seed=11)

    repository.update_section(section.section_id, "Food", 0)
    restored = repository.get_schedule(saved.schedule_id)
    assignment = restored.assignments[0]
    column_names = set(AssignmentRecord.__table__.columns.keys())

    assert assignment.occurrence.chore_id == chore.chore_id
    assert assignment.occurrence.section_id == section.section_id
    assert assignment.person_id == repository.list_people()[0].person_id
    assert assignment.occurrence.section_name == "Food"
    assert {"chore_id", "person_id", "section_id"} <= column_names
    assert not {"chore_name", "person_name", "time_label"} & column_names


def test_reorder_sections_and_chores_by_ids(repository):
    first_section = repository.add_section("First")
    second_section = repository.add_section("Second")
    first_chore = repository.add_chore(
        "Sweep",
        [ChoreOccurrence("Sweep", Weekday.MONDAY, "First")],
        section_id=first_section.section_id,
    )
    second_chore = repository.add_chore(
        "Mop",
        [ChoreOccurrence("Mop", Weekday.MONDAY, "First")],
        section_id=first_section.section_id,
    )

    reordered_chores = repository.reorder_chores(
        first_section.section_id,
        [second_chore.chore_id, first_chore.chore_id],
    )
    appended_chore = repository.add_chore(
        "Dust",
        [ChoreOccurrence("Dust", Weekday.TUESDAY, "First")],
        section_id=first_section.section_id,
    )
    reordered_sections = repository.reorder_sections(
        [second_section.section_id, first_section.section_id]
    )

    assert [(chore.name, chore.order_index) for chore in reordered_chores[:2]] == [
        ("Mop", 0),
        ("Sweep", 1),
    ]
    assert appended_chore.order_index == 2
    assert [(section.name, section.order_index) for section in reordered_sections] == [
        ("Second", 0),
        ("First", 1),
    ]


def test_existing_quoted_chore_and_schedule_values_are_migrated(tmp_path):
    database_path = tmp_path / "quoted.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            "CREATE TABLE people (id INTEGER PRIMARY KEY, name VARCHAR(120) UNIQUE);"
            "CREATE TABLE availability (id INTEGER PRIMARY KEY, person_id INTEGER, weekday VARCHAR(9), "
            "time_label VARCHAR(80));"
            "CREATE TABLE chores (id INTEGER PRIMARY KEY, name VARCHAR(160) UNIQUE, label VARCHAR(160));"
            "CREATE TABLE occurrences (id INTEGER PRIMARY KEY, chore_id INTEGER, weekday VARCHAR(9), "
            "time_label VARCHAR(80), start_minute INTEGER, end_minute INTEGER);"
            "CREATE TABLE schedules (id INTEGER PRIMARY KEY, created_at DATETIME, seed INTEGER);"
            "CREATE TABLE schedule_assignments (id INTEGER PRIMARY KEY, schedule_id INTEGER, chore_name VARCHAR(160), "
            "chore_label VARCHAR(160), weekday VARCHAR(9), time_label VARCHAR(80), start_minute INTEGER, "
            "end_minute INTEGER, person_name VARCHAR(120));"
            "INSERT INTO people VALUES (1, 'Alex');"
            "INSERT INTO availability VALUES (1, 1, 'Monday', '00:00-24:00');"
            "INSERT INTO chores VALUES (1, '''Dishes''', '""Kitchen""');"
            "INSERT INTO occurrences VALUES (1, 1, 'Monday', 'Morning', 540, 600);"
            "INSERT INTO occurrences VALUES (2, 1, 'Tuesday', 'Evening', 1080, 1200);"
            "INSERT INTO schedules VALUES (1, '2026-01-01 00:00:00', 3);"
            "INSERT INTO schedule_assignments VALUES (1, 1, '''Dishes''', '""Kitchen""', 'Monday', 'Morning', 540, 600, 'Alex');"
            "INSERT INTO schedule_assignments VALUES (2, 1, '''Dishes''', '""Kitchen""', 'Tuesday', 'Evening', 1080, 1200, 'Alex');"
        )
        connection.execute(
            "CREATE TABLE assignment_rules (id INTEGER PRIMARY KEY, person_id INTEGER UNIQUE, minimum INTEGER, maximum INTEGER)"
        )
        connection.commit()
    finally:
        connection.close()

    migrated = SQLiteRepository(database_path)
    try:
        chores = migrated.list_chores()
        assignments = migrated.get_schedule(1).assignments
        assert [(chore.name, chore.label, chore.section_name) for chore in chores] == [
            ("Dishes", "Kitchen", "Morning"),
            ("Dishes [Evening]", "Kitchen", "Evening"),
        ]
        assert migrated.list_people()[0].availability == {
            AvailabilitySlot(day, TimeRange(0, 1440)) for day in Weekday
        }
        assert [(assignment.occurrence.section_name, assignment.occurrence.chore_label) for assignment in assignments] == [
            ("Morning", "Kitchen"),
            ("Evening", "Kitchen"),
        ]
    finally:
        migrated.close()
