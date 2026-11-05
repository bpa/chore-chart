from fastapi.testclient import TestClient

from chore_chart.api import app


def test_api_manages_people_busy_times_rules_and_chores(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "api.sqlite3"))
    client = TestClient(app)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.post("/api/people", json={"name": "Alex"}).status_code == 200
    listed_person = client.get("/api/people").json()[0]
    assert listed_person["busy_times"] == []
    assert listed_person["cell_color"] == "#e7f1ec"
    assert listed_person["cell_color_mid"] is None
    assert listed_person["cell_color_end"] == "#dce8f1"
    assert listed_person["cell_fill"] == "solid"
    assert listed_person["cell_text_color"] == "#202c29"
    person_id = listed_person["person_id"]
    updated_style = client.put(
        f"/api/people/{person_id}/cell-style",
        json={"cell_color": "#f2c94c", "cell_color_mid": "#b7c9dc", "cell_color_end": "#ef8b67", "cell_fill": "linear-diagonal-up", "cell_text_color": "#ffffff"},
    )
    assert updated_style.json()["cell_color"] == "#f2c94c"
    assert updated_style.json()["cell_color_mid"] == "#b7c9dc"
    assert updated_style.json()["cell_color_end"] == "#ef8b67"
    assert updated_style.json()["cell_fill"] == "linear-diagonal-up"
    assert updated_style.json()["cell_text_color"] == "#ffffff"
    assert client.put(
        "/api/people/Alex/busy-times",
        json={"blocks": [{"weekday": "Monday", "time_range": "09:00-12:00", "label": "Work"}]},
    ).json()["busy_times"] == [{"weekday": "Monday", "time_range": "09:00-12:00", "label": "Work"}]
    assert client.put(
        "/api/rules/Alex", json={"minimum": 1, "maximum": 2}
    ).json() == {"person": "Alex", "minimum": 1, "maximum": 2}
    assert client.get("/api/rules").json() == [
        {"person": "Alex", "minimum": 1, "maximum": 2}
    ]

    section = client.post("/api/sections", json={"name": "Kitchen"}).json()
    chore_response = client.post(
        "/api/chores",
        json={
            "name": "Dishes",
            "label": "Dishes",
            "section_id": section["section_id"],
            "occurrences": [
                {
                    "weekday": "Monday",
                    "time_range": "09:00-10:00",
                }
            ],
        },
    )
    assert chore_response.status_code == 200
    chore_id = chore_response.json()["chore_id"]
    assert client.get("/api/chores").json()[0]["label"] == "Dishes"
    updated_chore = client.put(
        f"/api/chores/{chore_id}",
        json={
            "name": "Dishes",
            "label": "Dinner dishes",
            "section_id": section["section_id"],
            "occurrences": [{"weekday": "Tuesday"}],
        },
    )
    assert updated_chore.status_code == 200
    assert updated_chore.json()["label"] == "Dinner dishes"
    assert [item["weekday"] for item in updated_chore.json()["occurrences"]] == ["Tuesday"]
    assert client.delete(f"/api/chores/{chore_id}").json() == {
        "chore_id": chore_id,
        "name": "Dishes",
        "removed_occurrences": 1,
    }
    assert client.get("/api/chores").json() == []


def test_api_generates_lists_and_exports_saved_schedules(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "schedule.sqlite3"))
    client = TestClient(app)
    client.post("/api/people", json={"name": "Alex"})
    client.post("/api/people", json={"name": "Blair"})
    second_person = next(person for person in client.get("/api/people").json() if person["name"] == "Blair")
    section = client.post("/api/sections", json={"name": "Kitchen"}).json()
    client.post(
        "/api/chores",
        json={
            "name": "Dishes",
            "label": "Kitchen chore",
            "section_id": section["section_id"],
            "occurrences": [
                {
                    "weekday": "Monday",
                    "time_range": "09:00-10:00",
                }
            ],
        },
    )

    generated = client.post("/api/schedules/generate", json={"seed": 7})
    assert generated.status_code == 200
    schedule_id = generated.json()["schedule_id"]
    assert generated.json()["assignments"][0]["person"] == "Alex"
    assert generated.json()["assignments"][0]["chore"] == "Dishes"
    assert generated.json()["assignments"][0]["label"] == "Kitchen chore"
    assert generated.json()["assignments"][0]["section_id"] == section["section_id"]
    assert generated.json()["assignments"][0]["section"] == "Kitchen"
    assignment = generated.json()["assignments"][0]
    assert assignment["assignment_id"] > 0
    updated = client.put(
        f"/api/schedules/{schedule_id}/assignments",
        json={"assignments": [{"assignment_id": assignment["assignment_id"], "person_id": second_person["person_id"]}]},
    )
    assert updated.status_code == 200
    assert updated.json()["assignments"][0]["person"] == "Blair"
    assert client.get("/api/schedules").json()[0]["schedule_id"] == schedule_id
    assert client.get(f"/api/schedules/{schedule_id}").json() == updated.json()

    csv_response = client.get(f"/api/schedules/{schedule_id}/csv")
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert "Kitchen chore" in csv_response.text
    assert "Blair" in csv_response.text
    assert "Dishes" not in csv_response.text


def test_api_chore_rules_constrain_generation_and_manual_edits(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "assignment-rules.sqlite3"))
    client = TestClient(app)
    client.post("/api/people", json={"name": "Alex"})
    client.post("/api/people", json={"name": "Sam"})
    section = client.post("/api/sections", json={"name": "Kitchen"}).json()
    client.post(
        "/api/chores",
        json={
            "name": "Dishes",
            "section_id": section["section_id"],
            "occurrences": [{"weekday": "Monday"}],
        },
    )
    client.post(
        "/api/chores",
        json={
            "name": "Laundry",
            "section_id": section["section_id"],
            "occurrences": [{"weekday": "Tuesday"}],
        },
    )
    client.post(
        "/api/chores",
        json={
            "name": "Sweep",
            "section_id": section["section_id"],
            "occurrences": [{"weekday": "Wednesday"}],
        },
    )

    fixed_rule = client.post(
        "/api/assignment-rules",
        json={"person": "Alex", "operation": "is_assigned", "chore": "Dishes", "weekday": "Monday"},
    )
    prohibited_rule = client.post(
        "/api/assignment-rules",
        json={"person": "Alex", "operation": "may_not_be_assigned", "chore": "Laundry", "weekday": "Tuesday"},
    )
    assert fixed_rule.status_code == 200
    assert prohibited_rule.status_code == 200
    assert len(client.get("/api/assignment-rules").json()) == 2

    generated = client.post("/api/schedules/generate", json={}).json()
    assignments = {item["chore"]: item for item in generated["assignments"]}
    assert assignments["Dishes"]["person"] == "Alex"
    assert assignments["Laundry"]["person"] == "Sam"

    changed_fixed_assignment = client.put(
        f"/api/schedules/{generated['schedule_id']}/assignments",
        json={
            "assignments": [
                {"assignment_id": assignments["Dishes"]["assignment_id"], "person_id": 2},
                {"assignment_id": assignments["Laundry"]["assignment_id"], "person_id": 2},
                {"assignment_id": assignments["Sweep"]["assignment_id"], "person_id": 2},
            ]
        },
    )
    assert changed_fixed_assignment.status_code == 400
    assert "fixed" in changed_fixed_assignment.json()["detail"]

    other_person_id = 2 if assignments["Sweep"]["person_id"] == 1 else 1
    updated_schedule = client.put(
        f"/api/schedules/{generated['schedule_id']}/assignments",
        json={
            "assignments": [
                {"assignment_id": assignments["Dishes"]["assignment_id"], "person_id": 1},
                {"assignment_id": assignments["Laundry"]["assignment_id"], "person_id": 2},
                {"assignment_id": assignments["Sweep"]["assignment_id"], "person_id": other_person_id},
            ]
        },
    )
    assert updated_schedule.status_code == 200
    assert next(item for item in updated_schedule.json()["assignments"] if item["chore"] == "Sweep")["person_id"] == other_person_id

    assert client.delete(f"/api/assignment-rules/{fixed_rule.json()['rule_id']}").status_code == 200
    assert len(client.get("/api/assignment-rules").json()) == 1


def test_api_explains_why_schedule_generation_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "infeasible.sqlite3"))
    client = TestClient(app)
    client.post("/api/people", json={"name": "Alex"})
    section = client.post("/api/sections", json={"name": "Kitchen"}).json()
    for chore_name, time_range in (
        ("Dishes", "09:00-10:00"),
        ("Laundry", "09:30-10:30"),
    ):
        client.post(
            "/api/chores",
            json={
                "name": chore_name,
                "section_id": section["section_id"],
                "occurrences": [{"weekday": "Monday", "time_range": time_range}],
            },
        )

    response = client.post("/api/schedules/generate", json={})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Dishes" in detail
    assert "Laundry" in detail
    assert "09:00-10:00" in detail
    assert "09:30-10:30" in detail


def test_api_returns_client_errors_for_invalid_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "errors.sqlite3"))
    client = TestClient(app)

    assert client.put(
        "/api/people/Unknown/busy-times", json={"blocks": []}
    ).status_code == 400
    section = client.post("/api/sections", json={"name": "Home"}).json()
    assert client.post(
        "/api/chores",
        json={
            "name": "Dishes",
            "section_id": section["section_id"],
            "occurrences": [{"weekday": "Funday"}],
        },
    ).status_code == 400
    assert client.post(
        "/api/chores",
        json={
            "name": "Dishes",
            "section_id": 999,
            "occurrences": [{"weekday": "Monday"}],
        },
    ).status_code == 400
    assert client.get("/api/schedules/999").status_code == 404
    assert client.get("/api/schedules/999/csv").status_code == 404


def test_api_reorders_sections_and_chores_by_id(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "reorder.sqlite3"))
    client = TestClient(app)
    first_section = client.post("/api/sections", json={"name": "First"}).json()
    second_section = client.post("/api/sections", json={"name": "Second"}).json()

    chore_ids = []
    for name in ("Sweep", "Mop"):
        chore_ids.append(client.post(
            "/api/chores",
            json={
                "name": name,
                "section_id": first_section["section_id"],
                "occurrences": [{"weekday": "Monday"}],
            },
        ).json()["chore_id"])

    reordered_sections = client.put(
        "/api/sections/order",
        json={"section_ids": [second_section["section_id"], first_section["section_id"]]},
    )
    reordered_chores = client.put(
        f"/api/sections/{first_section['section_id']}/chores/order",
        json={"chore_ids": list(reversed(chore_ids))},
    )

    assert [section["name"] for section in reordered_sections.json()] == ["Second", "First"]
    assert [chore["name"] for chore in reordered_chores.json()] == ["Mop", "Sweep"]