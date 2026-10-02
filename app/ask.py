from app import llm, retrieve
from app.db import audit
from app.guardrails import REFUSAL, scrub, targets_other_client, values_in_spans


def _identifiers(conn, client_id: int | None) -> list[dict]:
    if client_id is None:
        rows = conn.execute(
            "SELECT mrn, full_name, email, phone, address FROM clients"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT mrn, full_name, email, phone, address FROM clients WHERE id != ?",
            (client_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _other_span_secrets(conn, client_id: int) -> list[tuple[str, str]]:
    rows = conn.execute(
        """SELECT text, pii_spans FROM chunks
           WHERE client_id != ? AND pii_spans != '[]'""",
        (client_id,),
    ).fetchall()
    secrets: list[tuple[str, str]] = []
    for row in rows:
        secrets.extend(values_in_spans(row["text"], row["pii_spans"]))
    return secrets


def _citations(hits: list[dict], others: list[dict], secrets: list[tuple[str, str]]) -> list[dict]:
    citations = []
    for hit in hits:
        snippet = scrub(hit["text"], others, secrets) if others or secrets else hit["text"]
        citations.append(
            {
                "chunk_id": hit["id"],
                "source_table": hit["source_table"],
                "source_id": hit["source_id"],
                "label": hit["label"],
                "snippet": snippet,
            }
        )
    return citations


def ask(conn, question: str, client_id: int | None) -> dict:
    audience = "customer" if client_id is not None else "admin"
    others = _identifiers(conn, client_id) if client_id is not None else []
    secrets = _other_span_secrets(conn, client_id) if client_id is not None else []
    if client_id is not None:
        self_row = conn.execute("SELECT mrn FROM clients WHERE id = ?", (client_id,)).fetchone()
        if self_row is None:
            raise ValueError("Unknown client.")
        if targets_other_client(question, self_row["mrn"], others, secrets):
            audit(conn, "customer", client_id, "query_refused", question)
            return {"answer": REFUSAL, "citations": [], "refused": True}

    hits = retrieve.search(conn, question, client_id=client_id)
    if client_id is not None:
        hits = [hit for hit in hits if hit["client_id"] == client_id]

    if not hits:
        audit(conn, audience, client_id, "query", question)
        return {
            "answer": "I cannot find that in the chart.",
            "citations": [],
            "refused": False,
        }

    answer = llm.complete(question, [hit["text"] for hit in hits], audience)
    if client_id is not None:
        answer = scrub(answer, others, secrets)
    audit(conn, audience, client_id, "query", question)
    return {
        "answer": answer,
        "citations": _citations(hits, others if client_id is not None else [], secrets),
        "refused": False,
    }
