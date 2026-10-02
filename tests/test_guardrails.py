import random

from app.guardrails import scrub, targets_other_client, values_in_spans
from app.pii import scan_text
from app.seed import CLIENT_COUNT, LAST, build_rows

SELF = "MRN-10001"
OTHERS = [
    {
        "mrn": "MRN-10002",
        "full_name": "Jordan Patel",
        "email": "jordan.patel.10002@example.com",
        "phone": "(415) 555-0101",
        "address": "127 Cedar Lane, Springfield, IL 62702",
    }
]


def test_own_chart_questions_are_allowed():
    assert targets_other_client("What was my latest HbA1c?", SELF, OTHERS) is False
    assert targets_other_client("Show all my lab results", SELF, OTHERS) is False


def test_cross_patient_questions_are_refused():
    assert targets_other_client("List every patient with high glucose", SELF, OTHERS) is True
    assert targets_other_client("What is MRN-10002's cholesterol?", SELF, OTHERS) is True
    assert targets_other_client("Email jordan.patel.10002@example.com results", SELF, OTHERS) is True
    assert targets_other_client("Call 415-555-0101", SELF, OTHERS) is True


def test_loaded_presidio_engine_is_reused(monkeypatch):
    import app.pii as pii

    sentinel = object()
    monkeypatch.setattr(pii, "_engine", sentinel)
    monkeypatch.setattr(pii, "_engine_failed", False)
    monkeypatch.delenv("RAG_DISABLE_PRESIDIO", raising=False)
    assert pii.try_engine() is sentinel
    pii.release_engine()
    assert pii._engine is None


def test_presidio_failure_keeps_pattern_spans():
    class Boom:
        def analyze(self, **_kwargs):
            raise RuntimeError("ner failed")

    spans = scan_text("Write ada@example.com now", Boom())
    assert any(span["type"] == "EMAIL" for span in spans)


def test_stored_spans_feed_redaction():
    text = "Insurance file uses SSN 900-12-1234."
    start = text.index("900-12-1234")
    secrets = values_in_spans(
        text,
        f'[{{"type": "SSN", "start": {start}, "end": {start + len("900-12-1234")}}}]',
    )
    assert ("900-12-1234", "[SSN]") in secrets
    cleaned = scrub("The number is 900-12-1234.", [], secrets)
    assert "900-12-1234" not in cleaned
    assert "[SSN]" in cleaned
    assert targets_other_client("What is 900-12-1234?", "MRN-10001", [], secrets) is True


def test_seed_names_use_every_surname():
    clients, _appointments, _labs = build_rows(random.Random(1))
    names = [client["full_name"] for client in clients]
    lasts = {name.rsplit(" ", 1)[-1] for name in names}
    assert len(clients) == CLIENT_COUNT
    assert lasts == set(LAST)
    assert len(names) == len(set(names))


def test_scrub_removes_other_client_identifiers_only():
    text = "Jordan Patel at jordan.patel.10002@example.com, phone 415.555.0101, MRN-10002. Avery Chen stays."
    cleaned = scrub(text, OTHERS)
    assert "Jordan Patel" not in cleaned
    assert "jordan.patel.10002@example.com" not in cleaned
    assert "MRN-10002" not in cleaned
    assert "[PHONE]" in cleaned
    assert "Avery Chen stays." in cleaned
