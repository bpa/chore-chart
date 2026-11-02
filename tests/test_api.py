from fastapi.testclient import TestClient

from chore_chart.api import app


def test_api_manages_people_busy_times_rules_and_chores(tmp_path, monkeypatch):
    monkeypatch.setenv("CHORE_CHART_DB", str(tmp_path / "api.sqlite3"))
    client = TestClient(app)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.post("/api/people", json={"name": "Alex"}).status_code == 200
    assert client.get("/api/people").json()[0]["busy_times"] == []
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
    assert client.get("/api/schedules").json()[0]["schedule_id"] == schedule_id
    assert client.get(f"/api/schedules/{schedule_id}").json() == generated.json()

    csv_response = client.get(f"/api/schedules/{schedule_id}/csv")
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert "Kitchen chore" in csv_response.text
    assert "Dishes" not in csv_response.text


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