import pytest

from chore_chart.domain import (
    AssignmentRule,
    BusyBlock,
    Chore,
    ChoreOccurrence,
    Person,
    TimeRange,
    Weekday,
)


def test_weekday_parse_accepts_names_and_values_case_insensitively():
    assert Weekday.parse("monday") is Weekday.MONDAY
    assert Weekday.parse("TUESDAY") is Weekday.TUESDAY
    assert Weekday.FRIDAY.order == 4


def test_weekday_parse_accepts_abbreviated_names():
    assert Weekday.parse("sa") is Weekday.SATURDAY
    assert Weekday.parse("th") is Weekday.THURSDAY
    assert Weekday.parse("su") is Weekday.SUNDAY


def test_weekday_parse_rejects_unknown_day():
    with pytest.raises(ValueError, match="Unknown weekday"):
        Weekday.parse("Funday")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BusyBlock(Weekday.MONDAY, TimeRange(600, 600)),
        lambda: ChoreOccurrence(" ", Weekday.MONDAY, "Morning"),
        lambda: ChoreOccurrence("Dishes", Weekday.MONDAY, " "),
        lambda: Person("  "),
        lambda: Chore("Dishes", ()),
        lambda: AssignmentRule("  "),
    ],
)
def test_domain_objects_reject_required_blank_values(factory):
    with pytest.raises(ValueError):
        factory()


def test_domain_objects_trim_names_and_labels():
    person = Person(" Alex ")
    slot = BusyBlock(Weekday.MONDAY, TimeRange.parse("16:00-19:00"), " Appointment ")
    occurrence = ChoreOccurrence(" Dishes ", Weekday.MONDAY, " Morning ")

    assert person.name == "Alex"
    assert str(slot.time_range) == "16:00-19:00"
    assert slot.label == "Appointment"
    assert occurrence.chore_name == "Dishes"
    assert occurrence.section_name == "Morning"


def test_time_range_parsing_and_overlap_minutes():
    availability = TimeRange.parse("16:00-19:00")

    assert availability.overlap_minutes(TimeRange.parse("15:00-17:00")) == 60
    assert availability.overlap_minutes(TimeRange.parse("19:00-20:00")) == 0
    assert str(TimeRange.parse("23:00-24:00")) == "23:00-24:00"
    with pytest.raises(ValueError, match="start before it ends"):
        TimeRange.parse("19:00-16:00")
    with pytest.raises(ValueError, match="24:00"):
        TimeRange.parse("23:00-24:30")


def test_chore_occurrence_must_belong_to_chore():
    with pytest.raises(ValueError, match="belong to its chore"):
        Chore("Dishes", (ChoreOccurrence("Laundry", Weekday.MONDAY, "Morning"),))


def test_chore_label_is_propagated_to_occurrences():
    chore = Chore(
        "trash_alex",
        (ChoreOccurrence("trash_alex", Weekday.MONDAY, "Anytime"),),
        label="Trash",
    )

    assert chore.occurrences[0].chore_label == "Trash"


def test_chore_names_and_labels_strip_matching_outer_quotes():
    occurrence = ChoreOccurrence(
        "'Dishes'",
        Weekday.MONDAY,
        "Morning",
        chore_label='"Kitchen"',
    )
    chore = Chore("'Dishes'", (occurrence,), label='"Kitchen"')

    assert chore.name == "Dishes"
    assert chore.label == "Kitchen"
    assert chore.occurrences[0].chore_name == "Dishes"
    assert chore.occurrences[0].chore_label == "Kitchen"


@pytest.mark.parametrize(("minimum", "maximum"), [(-1, None), (2, 1)])
def test_assignment_rule_rejects_invalid_count_bounds(minimum, maximum):
    with pytest.raises(ValueError):
        AssignmentRule("Alex", minimum, maximum)


def test_assignment_rule_accepts_valid_count_bounds():
    rule = AssignmentRule(" Alex ", minimum=1, maximum=3)

    assert rule.person_name == "Alex"
    assert rule.minimum == 1
    assert rule.maximum == 3