import pytest
from time import perf_counter

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
from chore_chart.scheduler import InfeasibleScheduleError, generate_schedule


def make_occurrence(chore_name, weekday=Weekday.MONDAY, time_label="Morning", time_range=None):
    return ChoreOccurrence(chore_name, weekday, time_label, time_range=time_range)


def make_person(name, *occurrences):
    availability = frozenset(
        AvailabilitySlot(occurrence.weekday, TimeRange(0, 1440))
        for occurrence in occurrences
    )
    return Person(name, availability)


def test_schedule_assigns_each_occurrence_to_an_available_person_within_bounds():
    dishes = make_occurrence("Dishes", Weekday.MONDAY)
    laundry = make_occurrence("Laundry", Weekday.TUESDAY, "Evening")
    people = [make_person("Alex", dishes, laundry), make_person("Sam", dishes, laundry)]
    chores = [Chore("Dishes", (dishes,)), Chore("Laundry", (laundry,))]

    schedule = generate_schedule(
        people,
        chores,
        [AssignmentRule("Alex", minimum=1, maximum=1), AssignmentRule("Sam", 1, 1)],
        seed=17,
    )

    assert len(schedule.assignments) == 2
    assert {item.person_name for item in schedule.assignments} == {"Alex", "Sam"}
    for assignment in schedule.assignments:
        person = next(person for person in people if person.name == assignment.person_name)
        assert any(
            slot.weekday == assignment.occurrence.weekday
            and (
                assignment.occurrence.time_range is None
                or slot.time_range.overlap_minutes(assignment.occurrence.time_range) >= 60
            )
            for slot in person.availability
        )


def test_schedule_is_reproducible_for_a_seed():
    occurrences = [make_occurrence("Dishes"), make_occurrence("Laundry", Weekday.TUESDAY)]
    people = [make_person("Alex", *occurrences), make_person("Sam", *occurrences)]
    chores = [Chore(item.chore_name, (item,)) for item in occurrences]

    first = generate_schedule(people, chores, seed=42)
    second = generate_schedule(people, chores, seed=42)

    assert first.assignments == second.assignments


def test_recent_history_discourages_repeating_person_chore_pairing():
    occurrence = make_occurrence("Dishes")
    people = [make_person("Alex", occurrence), make_person("Sam", occurrence)]
    history = [Schedule((Assignment(occurrence, "Alex"),))]

    schedule = generate_schedule(
        people,
        [Chore("Dishes", (occurrence,))],
        history=history,
        seed=1,
    )

    assert schedule.assignments[0].person_name == "Sam"


def test_same_label_is_not_assigned_to_one_person_twice_on_the_same_day():
    first = make_occurrence("trash_alex")
    second = make_occurrence("trash_sam")
    people = [make_person("Alex", first, second), make_person("Sam", first, second)]
    chores = [
        Chore("trash_alex", (first,), label="Trash"),
        Chore("trash_sam", (second,), label="Trash"),
    ]

    schedule = generate_schedule(
        people,
        chores,
        [AssignmentRule("Alex", 1, 1), AssignmentRule("Sam", 1, 1)],
        seed=3,
    )

    assert {item.person_name for item in schedule.assignments} == {"Alex", "Sam"}


def test_same_label_daily_rule_can_make_a_schedule_infeasible():
    first = make_occurrence("trash_alex")
    second = make_occurrence("trash_sam")
    people = [make_person("Alex", first, second), make_person("Sam", first, second)]
    chores = [
        Chore("trash_alex", (first,), label="Trash"),
        Chore("trash_sam", (second,), label="Trash"),
    ]

    with pytest.raises(InfeasibleScheduleError, match="No schedule satisfies"):
        generate_schedule(
            people,
            chores,
            [AssignmentRule("Alex", maximum=2), AssignmentRule("Sam", maximum=0)],
        )


def test_weekly_label_repeats_are_spread_across_available_people():
    occurrences = [
        make_occurrence("trash_monday", Weekday.MONDAY),
        make_occurrence("trash_tuesday", Weekday.TUESDAY),
    ]
    people = [make_person(name, *occurrences) for name in ("Alex", "Sam")]
    chores = [Chore(item.chore_name, (item,), label="Trash") for item in occurrences]
    history = [
        Schedule(
            (
                Assignment(make_occurrence("old_trash_monday"), "Sam"),
                Assignment(make_occurrence("old_trash_tuesday", Weekday.TUESDAY), "Sam"),
            )
        )
    ]

    schedule = generate_schedule(
        people,
        chores,
        [AssignmentRule("Alex", 1, 1), AssignmentRule("Sam", 1, 1)],
        history=history,
        seed=9,
    )

    assert {item.person_name for item in schedule.assignments} == {"Alex", "Sam"}


def test_label_repeat_objective_spreads_each_label_across_people():
    occurrences = [
        make_occurrence("alpha_monday", Weekday.MONDAY, time_label="Morning"),
        make_occurrence("alpha_tuesday", Weekday.TUESDAY, time_label="Morning"),
        make_occurrence("beta_wednesday", Weekday.WEDNESDAY, time_label="Evening"),
        make_occurrence("beta_thursday", Weekday.THURSDAY, time_label="Evening"),
    ]
    people = [make_person(name, *occurrences) for name in ("Alex", "Sam")]
    chores = [
        Chore(item.chore_name, (item,), label="Alpha" if item.chore_name.startswith("alpha") else "Beta")
        for item in occurrences
    ]

    schedule = generate_schedule(people, chores, seed=7)

    assignments_by_label: dict[str, set[str]] = {"Alpha": set(), "Beta": set()}
    for assignment in schedule.assignments:
        label = assignment.occurrence.chore_label
        assert label is not None
        assert assignment.person_name not in assignments_by_label[label]
        assignments_by_label[label].add(assignment.person_name)


def test_large_schedule_completes_within_thirty_seconds():
    people = [
        Person(
            f"P{index}",
            frozenset(AvailabilitySlot(day, TimeRange(0, 1440)) for day in Weekday),
        )
        for index in range(12)
    ]
    chores = []
    for index in range(160):
        occurrence = make_occurrence(
            f"Chore{index}",
            list(Weekday)[index % len(Weekday)],
            f"Group{index % 8}",
            TimeRange.parse(f"{15 + index % 6:02d}:00-{16 + index % 6:02d}:00"),
        )
        chores.append(Chore(occurrence.chore_name, (occurrence,), label=occurrence.time_label))

    started = perf_counter()
    schedule = generate_schedule(people, chores, seed=42)

    assert len(schedule.assignments) == len(chores)
    assert perf_counter() - started < 30


def test_no_available_person_reports_the_occurrence():
    occurrence = make_occurrence("Dishes")

    with pytest.raises(InfeasibleScheduleError, match="No available person.*Dishes.*Monday"):
        generate_schedule([Person("Alex")], [Chore("Dishes", (occurrence,))])


def test_availability_requires_at_least_one_hour_of_overlap():
    occurrence = make_occurrence(
        "Dishes",
        time_range=TimeRange.parse("17:00-18:30"),
    )
    available_person = Person(
        "Alex",
        frozenset({AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("16:00-18:00"))}),
    )
    short_overlap_person = Person(
        "Sam",
        frozenset({AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("16:00-17:59"))}),
    )

    schedule = generate_schedule([available_person], [Chore("Dishes", (occurrence,))])
    assert schedule.assignments[0].person_name == "Alex"
    with pytest.raises(InfeasibleScheduleError, match="at least one hour"):
        generate_schedule([short_overlap_person], [Chore("Dishes", (occurrence,))])


def test_untimed_chore_only_requires_availability_on_that_weekday():
    occurrence = make_occurrence("Dishes")
    person = Person(
        "Alex",
        frozenset({AvailabilitySlot(Weekday.MONDAY, TimeRange.parse("13:00-14:00"))}),
    )

    schedule = generate_schedule([person], [Chore("Dishes", (occurrence,))])

    assert schedule.assignments[0].person_name == "Alex"


def test_person_is_not_assigned_overlapping_timed_chores():
    first = make_occurrence("Dishes", time_range=TimeRange.parse("17:00-18:00"))
    second = make_occurrence("Laundry", time_range=TimeRange.parse("17:30-18:30"))

    with pytest.raises(InfeasibleScheduleError, match="No schedule satisfies"):
        generate_schedule(
            [make_person("Alex", first, second)],
            [Chore("Dishes", (first,)), Chore("Laundry", (second,))],
        )


def test_same_group_cannot_be_assigned_to_one_person_twice_on_one_day():
    first = make_occurrence("Prep", time_range=TimeRange.parse("16:00-17:00"))
    second = make_occurrence("Serve", time_range=TimeRange.parse("18:00-19:00"))

    with pytest.raises(InfeasibleScheduleError, match="No schedule satisfies"):
        generate_schedule(
            [make_person("Alex", first, second)],
            [Chore("Prep", (first,), label="Dinner"), Chore("Serve", (second,), label="Dinner")],
        )


def test_incompatible_minimums_report_infeasibility():
    occurrence = make_occurrence("Dishes")
    people = [make_person("Alex", occurrence), make_person("Sam", occurrence)]

    with pytest.raises(InfeasibleScheduleError, match="No schedule satisfies"):
        generate_schedule(
            people,
            [Chore("Dishes", (occurrence,))],
            [AssignmentRule("Alex", 1, 1), AssignmentRule("Sam", 1, 1)],
        )


@pytest.mark.parametrize(
    ("people", "chores", "message"),
    [
        ([], [Chore("Dishes", (make_occurrence("Dishes"),))], "at least one person"),
        ([Person("Alex")], [], "at least one chore"),
    ],
)
def test_schedule_requires_people_and_chores(people, chores, message):
    with pytest.raises(InfeasibleScheduleError, match=message):
        generate_schedule(people, chores)


def test_unknown_rule_person_is_rejected():
    occurrence = make_occurrence("Dishes")

    with pytest.raises(ValueError, match="unknown people"):
        generate_schedule(
            [make_person("Alex", occurrence)],
            [Chore("Dishes", (occurrence,))],
            [AssignmentRule("Sam")],
        )


def test_duplicate_person_names_are_rejected():
    occurrence = make_occurrence("Dishes")
    person = make_person("Alex", occurrence)

    with pytest.raises(ValueError, match="Person names must be unique"):
        generate_schedule([person, person], [Chore("Dishes", (occurrence,))])


def test_duplicate_occurrences_are_rejected():
    occurrence = make_occurrence("Dishes")

    with pytest.raises(ValueError, match="duplicate occurrences"):
        generate_schedule(
            [make_person("Alex", occurrence)],
            [Chore("Dishes", (occurrence, occurrence))],
        )


def test_minimum_above_eligible_occurrence_count_is_explained():
    occurrence = make_occurrence("Dishes")

    with pytest.raises(InfeasibleScheduleError, match="minimum of 2 exceeds"):
        generate_schedule(
            [make_person("Alex", occurrence)],
            [Chore("Dishes", (occurrence,))],
            [AssignmentRule("Alex", minimum=2)],
        )