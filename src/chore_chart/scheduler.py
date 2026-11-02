from __future__ import annotations

import random
from time import perf_counter
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import combinations

from ortools.sat.python import cp_model

from chore_chart.domain import (
    Assignment,
    AssignmentRule,
    Chore,
    ChoreOccurrence,
    Person,
    Schedule,
    Weekday,
)


class InfeasibleScheduleError(ValueError):
    pass


class ScheduleSearchLimitError(ValueError):
    pass


def _section_key(occurrence: ChoreOccurrence) -> int | str:
    return occurrence.section_id if occurrence.section_id is not None else occurrence.section_name.casefold()


def _schedule_failure_details(
    people: Sequence[Person],
    occurrences: Sequence[ChoreOccurrence],
    eligibility: dict[int, list[int]],
    rules: dict[str, AssignmentRule],
) -> str:
    details = [
        f"Problem size: {len(people)} people, {len(occurrences)} chore occurrences, "
        f"{sum(len(candidates) for candidates in eligibility.values())} eligible assignments."
    ]
    minimum_total = sum(rules.get(person.name, AssignmentRule(person.name)).minimum for person in people)
    maximum_total = sum(
        rule.maximum if (rule := rules.get(person.name)) is not None and rule.maximum is not None
        else len(occurrences)
        for person in people
    )
    if minimum_total > len(occurrences):
        details.append(
            f"Assignment minimums require {minimum_total} chores, but only "
            f"{len(occurrences)} occurrences exist."
        )
    if maximum_total < len(occurrences):
        details.append(
            f"Assignment maximums allow {maximum_total} chores, but all "
            f"{len(occurrences)} occurrences must be assigned."
        )

    for person_index, person in enumerate(people):
        rule = rules.get(person.name)
        if rule is not None and (rule.minimum or rule.maximum is not None):
            eligible_count = sum(
                person_index in candidates for candidates in eligibility.values()
            )
            maximum = str(rule.maximum) if rule.maximum is not None else "unlimited"
            details.append(
                f"{person.name}'s assignment rule is minimum {rule.minimum}, maximum {maximum}; "
                f"they are eligible for {eligible_count} occurrences."
            )

    conflict_count = 0
    for first_index, second_index in combinations(range(len(occurrences)), 2):
        first = occurrences[first_index]
        second = occurrences[second_index]
        same_day_label = (
            first.weekday == second.weekday
            and (first.chore_label or first.chore_name).casefold()
            == (second.chore_label or second.chore_name).casefold()
        )
        if not same_day_label:
            continue

        shared_people = set(eligibility[first_index]) & set(eligibility[second_index])
        if not shared_people:
            continue
        conflict_count += 1
        if conflict_count > 8:
            continue
        eligible_names = ", ".join(people[index].name for index in sorted(shared_people))
        if len(shared_people) == 1:
            shared_detail = f"{eligible_names} is the only shared eligible person"
        else:
            shared_detail = f"the shared eligible people are {eligible_names}"
        first_time = f", {first.time_range}" if first.time_range is not None else ""
        second_time = f", {second.time_range}" if second.time_range is not None else ""
        details.append(
            f"Conflict: {first.chore_name} ({first.weekday.value}{first_time}) and "
            f"{second.chore_name} ({second.weekday.value}{second_time}) cannot be assigned "
            f"to the same person because they share a chore label on the same day; {shared_detail}."
        )

    if conflict_count > 8:
        details.append(f"{conflict_count - 8} additional relevant conflicts were omitted.")
    if len(details) == 1:
        details.append(
            "The constraints conflict in combination; no single blocking rule or chore pair was identified."
        )
    return "\n".join(details)


def _heuristic_schedule(
    people: Sequence[Person],
    occurrences: Sequence[ChoreOccurrence],
    eligibility: dict[int, list[int]],
    rules: dict[str, AssignmentRule],
    history: Sequence[Schedule],
    seed: int | None,
) -> Schedule | None:
    rng = random.Random(seed)
    person_count = len(people)
    occurrence_count = len(occurrences)
    labels = [(item.chore_label or item.chore_name).casefold() for item in occurrences]
    sections = [_section_key(item) for item in occurrences]
    conflicts: list[set[int]] = [set() for _ in occurrences]
    day_groups: dict[Weekday, list[int]] = {}
    for occurrence_index, occurrence in enumerate(occurrences):
        day_groups.setdefault(occurrence.weekday, []).append(occurrence_index)
    section_groups: dict[int | str, list[int]] = {}
    for occurrence_index, section in enumerate(sections):
        section_groups.setdefault(section, []).append(occurrence_index)

    eligible_counts = [
        sum(person_index in candidates for candidates in eligibility.values())
        for person_index in range(person_count)
    ]
    section_eligible_counts = {
        section: [
            sum(person_index in eligibility[index] for index in occurrence_indices)
            for person_index in range(person_count)
        ]
        for section, occurrence_indices in section_groups.items()
    }
    daily_eligible_counts = {
        weekday: [
            sum(person_index in eligibility[index] for index in day_occurrences)
            for person_index in range(person_count)
        ]
        for weekday, day_occurrences in day_groups.items()
    }

    degrees = [0] * occurrence_count
    for day_occurrences in day_groups.values():
        for first_index, second_index in combinations(day_occurrences, 2):
            first = occurrences[first_index]
            second = occurrences[second_index]
            same_daily_label = labels[first_index] == labels[second_index]
            if same_daily_label:
                conflicts[first_index].add(second_index)
                conflicts[second_index].add(first_index)
                degrees[first_index] += 1
                degrees[second_index] += 1

    recent_loads: Counter[str] = Counter()
    repeated_chores: Counter[tuple[str, str]] = Counter()
    for prior_schedule in history[-4:]:
        for assignment in prior_schedule.assignments:
            recent_loads[assignment.person_name] += 1
            repeated_chores[(assignment.occurrence.chore_name, assignment.person_name)] += 1

    history_costs = {
        (occurrence_index, person_index): (
            recent_loads[people[person_index].name] * (occurrence_count + 1)
            + repeated_chores[(occurrences[occurrence_index].chore_name, people[person_index].name)] * 10
        )
        for occurrence_index, candidates in eligibility.items()
        for person_index in candidates
    }
    history_bound = sum(
        max(history_costs[(occurrence_index, person_index)] for person_index in candidates)
        for occurrence_index, candidates in eligibility.items()
    )
    label_sizes = Counter(labels)
    label_repeat_bound = sum(size * (size - 1) // 2 for size in label_sizes.values())
    section_sizes = Counter(sections)
    section_repeat_bound = sum(size * (size - 1) // 2 for size in section_sizes.values())
    day_sizes = Counter(item.weekday for item in occurrences)
    day_repeat_bound = sum(size * (size - 1) // 2 for size in day_sizes.values())
    daily_balance_bound = sum(
        len(day_occurrences) * max(first_count, second_count)
        for weekday, day_occurrences in day_groups.items()
        for first_count, second_count in combinations(daily_eligible_counts[weekday], 2)
    )
    section_balance_bound = sum(
        len(occurrence_indices) * max(first_count, second_count)
        for section, occurrence_indices in section_groups.items()
        for first_count, second_count in combinations(section_eligible_counts[section], 2)
    )
    label_repeat_weight = history_bound + 1
    section_repeat_weight = label_repeat_bound * label_repeat_weight + history_bound + 1
    day_repeat_weight = (
        section_repeat_bound * section_repeat_weight
        + label_repeat_bound * label_repeat_weight
        + history_bound
        + 1
    )
    daily_balance_weight = (
        day_repeat_bound * day_repeat_weight
        + section_repeat_bound * section_repeat_weight
        + label_repeat_bound * label_repeat_weight
        + history_bound
        + 1
    )
    section_balance_weight = (
        daily_balance_bound * daily_balance_weight
        + day_repeat_bound * day_repeat_weight
        + section_repeat_bound * section_repeat_weight
        + label_repeat_bound * label_repeat_weight
        + history_bound
        + 1
    )
    workload_weight = (
        section_balance_bound * section_balance_weight
        + daily_balance_bound * daily_balance_weight
        + day_repeat_bound * day_repeat_weight
        + section_repeat_bound * section_repeat_weight
        + label_repeat_bound * label_repeat_weight
        + history_bound
        + 1
    )

    minimums = [rules.get(person.name, AssignmentRule(person.name)).minimum for person in people]
    maximums = [
        rules.get(person.name, AssignmentRule(person.name)).maximum
        if rules.get(person.name, AssignmentRule(person.name)).maximum is not None
        else occurrence_count
        for person in people
    ]
    remaining_by_person = [
        sum(person_index in candidates for candidates in eligibility.values())
        for person_index in range(person_count)
    ]
    base_order = sorted(range(occurrence_count), key=lambda index: (len(eligibility[index]), -degrees[index]))
    best_assignments: list[int] | None = None
    best_score: int | None = None
    deadline = perf_counter() + 2.0

    for _ in range(12):
        if perf_counter() >= deadline:
            break
        assignments = [-1] * occurrence_count
        assigned_by_person: list[set[int]] = [set() for _ in people]
        loads = [0] * person_count
        daily_loads: list[Counter[Weekday]] = [Counter() for _ in people]
        labels_by_person: list[Counter[str]] = [Counter() for _ in people]
        sections_by_person: list[Counter[int | str]] = [Counter() for _ in people]
        remaining_domains = [set(candidates) for candidates in eligibility.values()]
        remaining_by_person = [
            sum(person_index in domain for domain in remaining_domains)
            for person_index in range(person_count)
        ]
        unassigned = set(range(occurrence_count))
        tie_breakers = {index: rng.random() for index in unassigned}
        failed = False

        while unassigned:
            occurrence_index = min(
                unassigned,
                key=lambda index: (
                    len(remaining_domains[index]),
                    -sum(neighbor in unassigned for neighbor in conflicts[index]),
                    tie_breakers[index] if index in tie_breakers else rng.random(),
                ),
            )
            occurrence = occurrences[occurrence_index]
            remaining_count = len(unassigned) - 1
            candidates = []
            for person_index in remaining_domains[occurrence_index]:
                if loads[person_index] >= maximums[person_index]:
                    continue
                if conflicts[occurrence_index] & assigned_by_person[person_index]:
                    continue
                blocked_neighbors = [
                    neighbor
                    for neighbor in conflicts[occurrence_index] & unassigned
                    if neighbor != occurrence_index and person_index in remaining_domains[neighbor]
                ]
                if any(len(remaining_domains[neighbor]) == 1 for neighbor in blocked_neighbors):
                    continue
                projected_loads = loads.copy()
                projected_loads[person_index] += 1
                remaining_after = remaining_by_person.copy()
                for available_person in remaining_domains[occurrence_index]:
                    remaining_after[available_person] -= 1
                remaining_after[person_index] -= len(blocked_neighbors)
                if any(
                    remaining_after[index] < max(0, minimums[index] - projected_loads[index])
                    for index in range(person_count)
                ):
                    continue
                if sum(maximums[index] - projected_loads[index] for index in range(person_count)) < remaining_count:
                    continue

                label_repeats = labels_by_person[person_index][labels[occurrence_index]]
                daily_load = daily_loads[person_index][occurrence.weekday]
                section_repeats = sections_by_person[person_index][sections[occurrence_index]]
                history_cost = history_costs[(occurrence_index, person_index)]
                candidates.append((
                    0 if loads[person_index] < minimums[person_index] else 1,
                    loads[person_index] / max(1, eligible_counts[person_index]),
                    sections_by_person[person_index][sections[occurrence_index]]
                    / max(1, section_eligible_counts[sections[occurrence_index]][person_index]),
                    daily_load / max(1, daily_eligible_counts[occurrence.weekday][person_index]),
                    section_repeats,
                    label_repeats,
                    history_cost,
                    rng.random(),
                    person_index,
                ))

            if not candidates:
                failed = True
                break
            person_index = min(candidates)[-1]
            assignments[occurrence_index] = person_index
            assigned_by_person[person_index].add(occurrence_index)
            loads[person_index] += 1
            daily_loads[person_index][occurrence.weekday] += 1
            labels_by_person[person_index][labels[occurrence_index]] += 1
            sections_by_person[person_index][sections[occurrence_index]] += 1
            unassigned.remove(occurrence_index)
            for available_person in remaining_domains[occurrence_index]:
                remaining_by_person[available_person] -= 1
            remaining_domains[occurrence_index].clear()
            for neighbor in conflicts[occurrence_index] & unassigned:
                if person_index in remaining_domains[neighbor]:
                    remaining_domains[neighbor].remove(person_index)
                    remaining_by_person[person_index] -= 1

        if failed or any(loads[index] < minimums[index] for index in range(person_count)):
            continue

        label_repeat_cost = sum(
            count * (count - 1) // 2
            for counts in labels_by_person
            for count in counts.values()
        )
        daily_repeat_cost = sum(
            count * (count - 1) // 2
            for counts in daily_loads
            for count in counts.values()
        )
        section_repeat_cost = sum(
            count * (count - 1) // 2
            for counts in sections_by_person
            for count in counts.values()
        )
        history_cost = sum(
            recent_loads[people[assignments[index]].name] * (occurrence_count + 1)
            + repeated_chores[(occurrences[index].chore_name, people[assignments[index]].name)] * 10
            for index in range(occurrence_count)
        )
        workload_balance_cost = sum(
            abs(
                loads[first_index] * eligible_counts[second_index]
                - loads[second_index] * eligible_counts[first_index]
            )
            for first_index, second_index in combinations(range(person_count), 2)
        )
        section_balance_cost = sum(
            abs(
                sections_by_person[first_index][section] * section_eligible_counts[section][second_index]
                - sections_by_person[second_index][section] * section_eligible_counts[section][first_index]
            )
            for section in section_groups
            for first_index, second_index in combinations(range(person_count), 2)
        )
        daily_balance_cost = sum(
            abs(
                daily_loads[first_index][weekday] * daily_eligible_counts[weekday][second_index]
                - daily_loads[second_index][weekday] * daily_eligible_counts[weekday][first_index]
            )
            for weekday in day_groups
            for first_index, second_index in combinations(range(person_count), 2)
        )
        score = workload_balance_cost * workload_weight
        score += section_balance_cost * section_balance_weight
        score += daily_balance_cost * daily_balance_weight
        score += daily_repeat_cost * day_repeat_weight
        score += section_repeat_cost * section_repeat_weight
        score += label_repeat_cost * label_repeat_weight + history_cost
        if best_score is None or score < best_score:
            best_score = score
            best_assignments = assignments

    if best_assignments is None:
        return None
    return Schedule(
        assignments=tuple(
            Assignment(occurrences[index], people[person_index].name, people[person_index].person_id)
            for index, person_index in enumerate(best_assignments)
        ),
        seed=seed,
    )


def generate_schedule(
    people: Sequence[Person],
    chores: Sequence[Chore],
    rules: Iterable[AssignmentRule] = (),
    history: Sequence[Schedule] = (),
    seed: int | None = None,
) -> Schedule:
    if not people:
        raise InfeasibleScheduleError("Add at least one person before generating a schedule.")
    if not chores:
        raise InfeasibleScheduleError("Add at least one chore before generating a schedule.")

    person_names = [person.name for person in people]
    if len(set(person_names)) != len(person_names):
        raise ValueError("Person names must be unique.")
    chore_names = [chore.name for chore in chores]
    if len(set(chore_names)) != len(chore_names):
        raise ValueError("Chore names must be unique.")

    occurrences = sorted(
        (occurrence for chore in chores for occurrence in chore.occurrences),
        key=lambda item: (
            item.weekday.order,
            item.time_range.start_minute if item.time_range else -1,
            item.section_name.casefold(),
            item.chore_name.casefold(),
        ),
    )
    occurrence_keys = [
        (item.chore_name.casefold(), item.weekday, item.section_name.casefold(), item.time_range)
        for item in occurrences
    ]
    if len(set(occurrence_keys)) != len(occurrence_keys):
        raise ValueError("A chore cannot have duplicate occurrences at the same weekday, group, and range.")

    rule_by_person = {rule.person_name: rule for rule in rules}
    unknown_people = set(rule_by_person) - set(person_names)
    if unknown_people:
        raise ValueError(f"Rules refer to unknown people: {', '.join(sorted(unknown_people))}.")

    eligibility: dict[int, list[int]] = {}
    for occurrence_index, occurrence in enumerate(occurrences):
        eligible_people = [
            person_index
            for person_index, person in enumerate(people)
            if _is_available(person, occurrence)
        ]
        if not eligible_people:
            time_description = (
                f"{occurrence.section_name}, {occurrence.time_range}"
                if occurrence.time_range is not None
                else occurrence.section_name
            )
            raise InfeasibleScheduleError(
                f"No available person for {occurrence.chore_name} on "
                f"{occurrence.weekday.value} ({time_description}); at least one hour of overlap is required."
            )
        eligibility[occurrence_index] = eligible_people

    heuristic_result = _heuristic_schedule(
        people,
        occurrences,
        eligibility,
        rule_by_person,
        history,
        seed,
    )
    if heuristic_result is not None:
        return heuristic_result

    assignment_variable_count = sum(len(candidates) for candidates in eligibility.values())
    if len(occurrences) > 200 or assignment_variable_count > 6000:
        raise ScheduleSearchLimitError(
            "The scheduler could not determine a valid assignment within its size limits: "
            f"{len(occurrences)} occurrences (limit 200) and {assignment_variable_count} "
            "eligible person-occurrence choices (limit 6000). This does not prove that no "
            "schedule exists. Reduce the problem size or narrow eligibility with busy times."
        )

    model = cp_model.CpModel()
    assigned: dict[tuple[int, int], cp_model.IntVar] = {}
    for occurrence_index, eligible_people in eligibility.items():
        variables = []
        for person_index in eligible_people:
            variable = model.new_bool_var(f"occ_{occurrence_index}_person_{person_index}")
            assigned[(occurrence_index, person_index)] = variable
            variables.append(variable)
        model.add_exactly_one(variables)

    label_day_groups: dict[tuple[Weekday, str], list[int]] = {}
    label_groups: dict[str, list[int]] = {}
    section_groups: dict[int | str, list[int]] = {}
    for occurrence_index, occurrence in enumerate(occurrences):
        label = (occurrence.chore_label or occurrence.chore_name).casefold()
        label_day_groups.setdefault((occurrence.weekday, label), []).append(occurrence_index)
        label_groups.setdefault(label, []).append(occurrence_index)
        section_groups.setdefault(_section_key(occurrence), []).append(occurrence_index)

    for occurrence_indices in label_day_groups.values():
        for first_index, second_index in combinations(occurrence_indices, 2):
            for person_index in sorted(set(eligibility[first_index]) & set(eligibility[second_index])):
                model.add(
                    assigned[(first_index, person_index)] + assigned[(second_index, person_index)] <= 1
                )

    label_repeat_variables = []
    for occurrence_indices in label_groups.values():
        for first_index, second_index in combinations(occurrence_indices, 2):
            for person_index in sorted(set(eligibility[first_index]) & set(eligibility[second_index])):
                repeated = model.new_bool_var(
                    f"label_repeat_{first_index}_{second_index}_person_{person_index}"
                )
                first_assignment = assigned[(first_index, person_index)]
                second_assignment = assigned[(second_index, person_index)]
                model.add(repeated <= first_assignment)
                model.add(repeated <= second_assignment)
                model.add(repeated >= first_assignment + second_assignment - 1)
                label_repeat_variables.append(repeated)

    section_repeat_variables = []
    for occurrence_indices in section_groups.values():
        for first_index, second_index in combinations(occurrence_indices, 2):
            for person_index in sorted(set(eligibility[first_index]) & set(eligibility[second_index])):
                repeated = model.new_bool_var(
                    f"section_repeat_{first_index}_{second_index}_person_{person_index}"
                )
                first_assignment = assigned[(first_index, person_index)]
                second_assignment = assigned[(second_index, person_index)]
                model.add(repeated <= first_assignment)
                model.add(repeated <= second_assignment)
                model.add(repeated >= first_assignment + second_assignment - 1)
                section_repeat_variables.append(repeated)

    day_repeat_variables = []
    day_groups: dict[Weekday, list[int]] = {}
    for occurrence_index, occurrence in enumerate(occurrences):
        day_groups.setdefault(occurrence.weekday, []).append(occurrence_index)
    for occurrence_indices in day_groups.values():
        for first_index, second_index in combinations(occurrence_indices, 2):
            for person_index in sorted(set(eligibility[first_index]) & set(eligibility[second_index])):
                repeated = model.new_bool_var(
                    f"day_repeat_{first_index}_{second_index}_person_{person_index}"
                )
                first_assignment = assigned[(first_index, person_index)]
                second_assignment = assigned[(second_index, person_index)]
                model.add(repeated <= first_assignment)
                model.add(repeated <= second_assignment)
                model.add(repeated >= first_assignment + second_assignment - 1)
                day_repeat_variables.append(repeated)

    loads = []
    for person_index, person in enumerate(people):
        person_variables = [
            variable
            for (occurrence_index, candidate_index), variable in assigned.items()
            if candidate_index == person_index
        ]
        rule = rule_by_person.get(person.name, AssignmentRule(person.name))
        if rule.minimum > len(person_variables):
            raise InfeasibleScheduleError(
                f"{person.name}'s minimum of {rule.minimum} exceeds their "
                f"{len(person_variables)} eligible chore occurrence(s)."
            )
        load = model.new_int_var(0, len(occurrences), f"load_{person_index}")
        model.add(load == sum(person_variables))
        model.add(load >= rule.minimum)
        if rule.maximum is not None:
            model.add(load <= rule.maximum)
        loads.append(load)

    eligible_counts = [
        sum(person_index in candidates for candidates in eligibility.values())
        for person_index in range(len(people))
    ]
    workload_balance_variables = []
    for first_index, second_index in combinations(range(len(people)), 2):
        first_count = eligible_counts[first_index]
        second_count = eligible_counts[second_index]
        if not first_count or not second_count:
            continue
        bound = len(occurrences) * max(first_count, second_count)
        difference = model.new_int_var(0, bound, f"workload_balance_{first_index}_{second_index}")
        model.add_abs_equality(
            difference,
            loads[first_index] * second_count - loads[second_index] * first_count,
        )
        workload_balance_variables.append(difference)

    section_balance_variables = []
    for section, occurrence_indices in section_groups.items():
        section_counts = [
            sum(person_index in eligibility[index] for index in occurrence_indices)
            for person_index in range(len(people))
        ]
        for first_index, second_index in combinations(range(len(people)), 2):
            first_count = section_counts[first_index]
            second_count = section_counts[second_index]
            if not first_count or not second_count:
                continue
            first_load = sum(
                assigned[(occurrence_index, first_index)]
                for occurrence_index in occurrence_indices
                if (occurrence_index, first_index) in assigned
            )
            second_load = sum(
                assigned[(occurrence_index, second_index)]
                for occurrence_index in occurrence_indices
                if (occurrence_index, second_index) in assigned
            )
            bound = len(occurrence_indices) * max(first_count, second_count)
            difference = model.new_int_var(
                0,
                bound,
                f"section_balance_{section}_{first_index}_{second_index}",
            )
            model.add_abs_equality(
                difference,
                first_load * second_count - second_load * first_count,
            )
            section_balance_variables.append(difference)

    daily_balance_variables = []
    for weekday, occurrence_indices in day_groups.items():
        daily_counts = [
            sum(person_index in eligibility[index] for index in occurrence_indices)
            for person_index in range(len(people))
        ]
        for first_index, second_index in combinations(range(len(people)), 2):
            first_count = daily_counts[first_index]
            second_count = daily_counts[second_index]
            if not first_count or not second_count:
                continue
            first_load = sum(
                assigned[(occurrence_index, first_index)]
                for occurrence_index in occurrence_indices
                if (occurrence_index, first_index) in assigned
            )
            second_load = sum(
                assigned[(occurrence_index, second_index)]
                for occurrence_index in occurrence_indices
                if (occurrence_index, second_index) in assigned
            )
            bound = len(occurrence_indices) * max(first_count, second_count)
            difference = model.new_int_var(
                0,
                bound,
                f"daily_balance_{weekday.value}_{first_index}_{second_index}",
            )
            model.add_abs_equality(
                difference,
                first_load * second_count - second_load * first_count,
            )
            daily_balance_variables.append(difference)

    recent_loads: Counter[str] = Counter()
    repeat_counts: Counter[tuple[str, str]] = Counter()
    for prior_schedule in history[-4:]:
        for assignment in prior_schedule.assignments:
            recent_loads[assignment.person_name] += 1
            repeat_counts[(assignment.occurrence.chore_name, assignment.person_name)] += 1

    rng = random.Random(seed)
    tie_costs = {
        key: rng.randrange(4)
        for key in assigned
    }
    history_costs = {
        (occurrence_index, person_index): (
            recent_loads[people[person_index].name] * (len(occurrences) + 1)
            + repeat_counts[(occurrences[occurrence_index].chore_name,
                             people[person_index].name)] * 10
            + tie_costs[(occurrence_index, person_index)]
        )
        for occurrence_index, person_index in assigned
    }
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed if seed is not None else 0
    solver.parameters.num_search_workers = 8
    objectives = [
        sum(workload_balance_variables),
        sum(section_balance_variables),
        sum(daily_balance_variables),
        sum(day_repeat_variables),
        sum(section_repeat_variables),
        sum(label_repeat_variables),
    ]
    objectives = [
        objective
        for variables, objective in zip(
            (
                workload_balance_variables,
                section_balance_variables,
                daily_balance_variables,
                day_repeat_variables,
                section_repeat_variables,
                label_repeat_variables,
            ),
            objectives,
        )
        if variables
    ]
    history_objective = sum(
        history_costs[(occurrence_index, person_index)] * variable
        for (occurrence_index, person_index), variable in assigned.items()
    )
    if any(history_costs.values()):
        objectives.append(history_objective)
    if not objectives:
        objectives.append(0)

    deadline = perf_counter() + 8.0
    best_assignment: list[int] | None = None
    for objective_index, objective in enumerate(objectives):
        remaining_time = deadline - perf_counter()
        if remaining_time <= 0:
            break
        model.minimize(objective)
        solver.parameters.max_time_in_seconds = remaining_time
        result = solver.solve(model)
        if result == cp_model.INFEASIBLE:
            if best_assignment is None:
                raise InfeasibleScheduleError(
                    "No schedule satisfies everyone's busy times and assignment-count rules.\n"
                    + _schedule_failure_details(people, occurrences, eligibility, rule_by_person)
                )
            break
        if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if best_assignment is not None:
                break
            if result == cp_model.UNKNOWN:
                raise ScheduleSearchLimitError(
                    "The scheduler did not find a valid assignment within its 8-second search limit, "
                    "but it could not prove that the configuration is infeasible. Try simplifying "
                    "the schedule or narrowing eligibility with busy times."
                )
            raise ScheduleSearchLimitError(
                f"The scheduler stopped with solver status {solver.status_name(result)}. "
                "No schedule was created; review the schedule configuration and try again."
            )

        best_assignment = [
            next(
                person_index
                for person_index in eligibility[occurrence_index]
                if solver.boolean_value(assigned[(occurrence_index, person_index)])
            )
            for occurrence_index in range(len(occurrences))
        ]
        if result != cp_model.OPTIMAL:
            break
        if objective_index + 1 < len(objectives) and not isinstance(objective, int):
            model.add(objective == solver.value(objective))

    if best_assignment is None:
        raise ScheduleSearchLimitError(
            "The scheduler did not find a valid assignment within its 8-second search limit, "
            "but it could not prove that the configuration is infeasible. Try simplifying "
            "the schedule or narrowing eligibility with busy times."
        )

    assignments = tuple(
        Assignment(
            occurrence=occurrence,
            person_name=people[best_assignment[occurrence_index]].name,
            person_id=people[best_assignment[occurrence_index]].person_id,
        )
        for occurrence_index, occurrence in enumerate(occurrences)
    )
    return Schedule(assignments=assignments, seed=seed)


def _is_available(person: Person, occurrence: ChoreOccurrence) -> bool:
    busy = sorted(
        (block.time_range for block in person.busy_times if block.weekday == occurrence.weekday),
        key=lambda time_range: time_range.start_minute,
    )
    if occurrence.time_range is not None:
        window = occurrence.time_range
        covered_until = window.start_minute
        for time_range in busy:
            if time_range.end_minute <= covered_until:
                continue
            if time_range.start_minute > covered_until:
                return True
            covered_until = max(covered_until, time_range.end_minute)
            if covered_until >= window.end_minute:
                return False
        return covered_until < window.end_minute

    covered_until = 0
    for time_range in busy:
        if time_range.start_minute > covered_until:
            break
        covered_until = max(covered_until, time_range.end_minute)
        if covered_until == 1440:
            return False
    return True