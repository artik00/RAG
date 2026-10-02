import hashlib
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.records import create_appointment, create_bloodwork, create_client


def fake_embed(texts):
    vectors = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
        vector = rng.standard_normal(8).astype(np.float32)
        vector /= max(float(np.linalg.norm(vector)), 1e-9)
        vectors.append(vector)
    return np.vstack(vectors)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DB", str(tmp_path / "rag.db"))
    monkeypatch.setenv("RAG_DISABLE_PRESIDIO", "1")
    monkeypatch.setattr("app.embeddings.embed_texts", fake_embed)
    monkeypatch.setattr(
        "app.llm.complete",
        lambda question, passages, audience: "Chart answer.",
    )
    init_db()
    with TestClient(app_module()) as client:
        yield client


def app_module():
    from app.main import app
    return app


def staff(api):
    response = api.post("/admin/v1/login", json={"password": "admin"})
    assert response.status_code == 200


def test_customer_chart_is_limited_to_self(api):
    staff(api)
    first = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Avery Chen",
            "date_of_birth": "1984-03-12",
            "email": "avery@example.com",
            "phone": "(415) 555-0100",
            "address": "120 Cedar Lane",
        },
    )
    second = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Jordan Patel",
            "date_of_birth": "1990-07-02",
            "email": "jordan@example.com",
            "phone": "(415) 555-0101",
            "address": "127 Cedar Lane",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201
    api.post(
        "/admin/v1/bloodwork",
        json={
            "client_id": second.json()["id"],
            "collected_at": "2024-05-01",
            "panel_name": "Endocrine",
            "analyte": "SecretMarker",
            "value": 9.5,
            "unit": "%",
            "ref_low": 4.0,
            "ref_high": 5.6,
        },
    )
    api.post("/admin/v1/logout")
    signed = api.post(
        "/customer/v1/login",
        json={"mrn": first.json()["mrn"], "password": "demo"},
    )
    assert signed.status_code == 200
    chart = api.get("/customer/v1/chart")
    assert chart.status_code == 200
    assert chart.json()["client"]["mrn"] == first.json()["mrn"]
    assert chart.json()["bloodwork"] == []
    blocked = api.get("/admin/v1/clients")
    assert blocked.status_code == 401


def test_cross_patient_question_does_not_call_the_model(api, monkeypatch):
    staff(api)
    created = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Avery Chen",
            "date_of_birth": "1984-03-12",
            "email": "avery@example.com",
            "phone": "(415) 555-0100",
            "address": "120 Cedar Lane",
        },
    )
    other = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Jordan Patel",
            "date_of_birth": "1990-07-02",
            "email": "jordan@example.com",
            "phone": "(415) 555-0101",
            "address": "127 Cedar Lane",
        },
    )
    api.post("/admin/v1/logout")
    api.post("/customer/v1/login", json={"mrn": created.json()["mrn"], "password": "demo"})

    def explode(*_args, **_kwargs):
        raise AssertionError("model should not run")

    monkeypatch.setattr("app.llm.complete", explode)
    response = api.post(
        "/customer/v1/query",
        json={"question": f"What is {other.json()['mrn']}'s glucose?"},
    )
    assert response.status_code == 200
    assert response.json()["refused"] is True
    assert response.json()["citations"] == []


def test_answer_scrubs_another_clients_email(api, monkeypatch):
    staff(api)
    created = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Avery Chen",
            "date_of_birth": "1984-03-12",
            "email": "avery@example.com",
            "phone": "(415) 555-0100",
            "address": "120 Cedar Lane",
        },
    )
    other = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Jordan Patel",
            "date_of_birth": "1990-07-02",
            "email": "jordan@example.com",
            "phone": "(415) 555-0101",
            "address": "127 Cedar Lane",
        },
    )
    api.post(
        "/admin/v1/bloodwork",
        json={
            "client_id": created.json()["id"],
            "collected_at": "2024-05-01",
            "panel_name": "Endocrine",
            "analyte": "HbA1c",
            "value": 6.4,
            "unit": "%",
            "ref_low": 4.0,
            "ref_high": 5.6,
        },
    )
    api.post("/admin/v1/logout")
    api.post("/customer/v1/login", json={"mrn": created.json()["mrn"], "password": "demo"})
    monkeypatch.setattr(
        "app.llm.complete",
        lambda question, passages, audience: f"See {other.json()['email']} for the result.",
    )
    response = api.post("/customer/v1/query", json={"question": "What was my HbA1c?"})
    assert response.status_code == 200
    body = response.json()
    assert "jordan@example.com" not in body["answer"]
    assert "[EMAIL]" in body["answer"]
    assert all(item["source_table"] == "bloodwork" or item["source_table"] == "clients" for item in body["citations"])


def test_answer_scrubs_ssn_detected_on_another_chart(api, monkeypatch):
    staff(api)
    created = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Avery Chen",
            "date_of_birth": "1984-03-12",
            "email": "avery@example.com",
            "phone": "(415) 555-0100",
            "address": "120 Cedar Lane",
        },
    )
    other = api.post(
        "/admin/v1/clients",
        json={
            "full_name": "Jordan Patel",
            "date_of_birth": "1990-07-02",
            "email": "jordan@example.com",
            "phone": "(415) 555-0101",
            "address": "127 Cedar Lane",
        },
    )
    noted = api.post(
        "/admin/v1/appointments",
        json={
            "client_id": other.json()["id"],
            "appointment_date": "2024-05-01",
            "clinician_name": "Dr. Elena Vasquez",
            "reason": "Lab review",
            "note": "Insurance file uses SSN 900-12-1234.",
            "diagnosis": "Health maintenance",
        },
    )
    assert noted.status_code == 201
    api.post(
        "/admin/v1/bloodwork",
        json={
            "client_id": created.json()["id"],
            "collected_at": "2024-05-01",
            "panel_name": "Endocrine",
            "analyte": "HbA1c",
            "value": 6.4,
            "unit": "%",
            "ref_low": 4.0,
            "ref_high": 5.6,
        },
    )
    api.post("/admin/v1/logout")
    api.post("/customer/v1/login", json={"mrn": created.json()["mrn"], "password": "demo"})
    monkeypatch.setattr(
        "app.llm.complete",
        lambda question, passages, audience: "Copied 900-12-1234 from another note.",
    )
    response = api.post("/customer/v1/query", json={"question": "What was my HbA1c?"})
    assert response.status_code == 200
    assert "900-12-1234" not in response.json()["answer"]
    assert "[SSN]" in response.json()["answer"]


def test_parallel_client_creates_get_distinct_mrns(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DB", str(tmp_path / "rag.db"))
    monkeypatch.setenv("RAG_DISABLE_PRESIDIO", "1")
    monkeypatch.setattr("app.embeddings.embed_texts", fake_embed)
    init_db()
    barrier = threading.Barrier(2)
    found: list[str] = []
    errors: list[BaseException] = []

    def make(name: str, email: str) -> None:
        try:
            barrier.wait(timeout=5)
            with connect() as conn:
                client = create_client(
                    conn,
                    full_name=name,
                    date_of_birth="1984-03-12",
                    email=email,
                    phone="(415) 555-0198",
                    address="1 Test Lane",
                )
                found.append(client["mrn"])
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=make, args=("Avery Chen", "avery-race@example.com")),
        threading.Thread(target=make, args=("Jordan Patel", "jordan-race@example.com")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(found) == 2
    assert len(set(found)) == 2


def test_search_cannot_return_another_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DB", str(tmp_path / "rag.db"))
    monkeypatch.setenv("RAG_DISABLE_PRESIDIO", "1")
    init_db()
    with connect() as conn:
        first = create_client(
            conn,
            full_name="Avery Chen",
            date_of_birth="1984-03-12",
            email="avery@example.com",
            phone="(415) 555-0100",
            address="120 Cedar Lane",
            mrn="MRN-10001",
            vector=np.array([1, 0], dtype=np.float32),
            spans=[],
        )
        second = create_client(
            conn,
            full_name="Jordan Patel",
            date_of_birth="1990-07-02",
            email="jordan@example.com",
            phone="(415) 555-0101",
            address="127 Cedar Lane",
            mrn="MRN-10002",
            vector=np.array([0, 1], dtype=np.float32),
            spans=[],
        )
        create_bloodwork(
            conn,
            client_id=second["id"],
            collected_at="2024-05-01",
            panel_name="Endocrine",
            analyte="HbA1c",
            value=8,
            unit="%",
            ref_low=4,
            ref_high=5.6,
            vector=np.array([0, 1], dtype=np.float32),
            spans=[],
        )
    monkeypatch.setattr(
        "app.embeddings.embed_texts",
        lambda texts: np.array([[0, 1]], dtype=np.float32),
    )
    from app.retrieve import search

    with connect() as conn:
        hits = search(conn, "HbA1c", client_id=first["id"], k=3)
    assert hits
    assert all(hit["client_id"] == first["id"] for hit in hits)


def test_appointment_cannot_link_another_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DB", str(tmp_path / "rag.db"))
    monkeypatch.setenv("RAG_DISABLE_PRESIDIO", "1")
    monkeypatch.setattr("app.embeddings.embed_texts", fake_embed)
    init_db()
    with connect() as conn:
        first = create_client(
            conn,
            full_name="Avery Chen",
            date_of_birth="1984-03-12",
            email="avery@example.com",
            phone="(415) 555-0100",
            address="120 Cedar Lane",
            mrn="MRN-10001",
        )
        second = create_client(
            conn,
            full_name="Jordan Patel",
            date_of_birth="1990-07-02",
            email="jordan@example.com",
            phone="(415) 555-0101",
            address="127 Cedar Lane",
            mrn="MRN-10002",
        )
        visit = create_appointment(
            conn,
            client_id=first["id"],
            appointment_date="2024-01-02",
            clinician_name="Dr. Elena Vasquez",
            reason="Lab review",
            note="Reviewed prior labs.",
            diagnosis="Health maintenance",
        )
        with pytest.raises(ValueError):
            create_bloodwork(
                conn,
                client_id=second["id"],
                appointment_id=visit["id"],
                collected_at="2024-01-02",
                panel_name="CBC",
                analyte="WBC",
                value=5,
                unit="10^3/uL",
                ref_low=4,
                ref_high=11,
            )
