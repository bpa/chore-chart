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
    conflicts: list[set[int]] = [set() for _ in occurrences]
    day_groups: dict[Weekday, list[int]] = {}
    for occurrence_index, occurrence in enumerate(occurrences):
        day_groups.setdefault(occurrence.weekday, []).append(occurrence_index)

    degrees = [0] * occurrence_count
    for day_occurrences in day_groups.values():
        for first_index, second_index in combinations(day_occurrences, 2):
            first = occurrences[first_index]
            second = occurrences[second_index]
            timed_overlap = (
                first.time_range is not None
                and second.time_range is not None
                and first.time_range.overlap_minutes(second.time_range) > 0
            )
            same_daily_label = labels[first_index] == labels[second_index]
            if timed_overlap or same_daily_label:
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
    label_repeat_weight = history_bound + 1
    workload_weight = label_repeat_bound * label_repeat_weight + history_bound + 1

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
        labels_by_person: list[Counter[str]] = [Counter() for _ in people]
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
                history_cost = history_costs[(occurrence_index, person_index)]
                candidates.append((
                    0 if loads[person_index] < minimums[person_index] else 1,
                    loads[person_index],
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
            labels_by_person[person_index][labels[occurrence_index]] += 1
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
        history_cost = sum(
            recent_loads[people[assignments[index]].name] * (occurrence_count + 1)
            + repeated_chores[(occurrences[index].chore_name, people[assignments[index]].name)] * 10
            for index in range(occurrence_count)
        )
        score = (max(loads) - min(loads)) * workload_weight
        score += label_repeat_cost * label_repeat_weight + history_cost
        if best_score is None or score < best_score:
            best_score = score
            best_assignments = assignments

    if best_assignments is None:
        return None
    return Schedule(
        assignments=tuple(
            Assignment(occurrences[index], people[person_index].name)
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
            item.time_label.casefold(),
            item.chore_name.casefold(),
        ),
    )
    occurrence_keys = [
        (item.chore_name.casefold(), item.weekday, item.time_label.casefold(), item.time_range)
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
            if any(
                available.weekday == occurrence.weekday
                and (
                    occurrence.time_range is None
                    or available.time_range.overlap_minutes(occurrence.time_range) >= 60
                )
                for available in person.availability
            )
        ]
        if not eligible_people:
            time_description = (
                f"{occurrence.time_label}, {occurrence.time_range}"
                if occurrence.time_range is not None
                else occurrence.time_label
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
    if len(occurrences) > 120 or assignment_variable_count > 6000:
        raise ScheduleSearchLimitError(
            "The bounded scheduler could not find a valid assignment for this large problem. "
            "Review availability, rules, or reduce the number of occurrences."
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

    for first_index, second_index in combinations(range(len(occurrences)), 2):
        first = occurrences[first_index]
        second = occurrences[second_index]
        if (
            first.weekday != second.weekday
            or first.time_range is None
            or second.time_range is None
            or first.time_range.overlap_minutes(second.time_range) == 0
        ):
            continue
        for person_index in sorted(set(eligibility[first_index]) & set(eligibility[second_index])):
            model.add(
                assigned[(first_index, person_index)] + assigned[(second_index, person_index)] <= 1
            )

    label_day_groups: dict[tuple[Weekday, str], list[int]] = {}
    label_groups: dict[str, list[int]] = {}
    for occurrence_index, occurrence in enumerate(occurrences):
        label = (occurrence.chore_label or occurrence.chore_name).casefold()
        label_day_groups.setdefault((occurrence.weekday, label), []).append(occurrence_index)
        label_groups.setdefault(label, []).append(occurrence_index)

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

    max_load = model.new_int_var(0, len(occurrences), "max_load")
    min_load = model.new_int_var(0, len(occurrences), "min_load")
    model.add_max_equality(max_load, loads)
    model.add_min_equality(min_load, loads)

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
    history_bound = sum(
        max(
            history_costs[(occurrence_index, person_index)]
            for person_index in eligible_people
        )
        for occurrence_index, eligible_people in eligibility.items()
    )
    label_repeat_weight = history_bound + 1
    workload_weight = len(label_repeat_variables) * label_repeat_weight + history_bound + 1
    model.minimize(
        (max_load - min_load) * workload_weight
        + sum(label_repeat_variables) * label_repeat_weight
        + sum(
            history_costs[(occurrence_index, person_index)] * variable
            for (occurrence_index, person_index), variable in assigned.items()
        )
    )

    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed if seed is not None else 0
    solver.parameters.num_search_workers = 8
    solver.parameters.max_time_in_seconds = 8.0
    result = solver.solve(model)
    if result not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise InfeasibleScheduleError(
            "No schedule satisfies everyone's availability and assignment-count rules. "
            "Review availability, minimums, and maximums."
        )

    assignments = tuple(
        Assignment(
            occurrence=occurrence,
            person_name=people[
                next(
                    person_index
                    for person_index in eligibility[occurrence_index]
                    if solver.boolean_value(assigned[(occurrence_index, person_index)])
                )
            ].name,
        )
        for occurrence_index, occurrence in enumerate(occurrences)
    )
    return Schedule(assignments=assignments, seed=seed)