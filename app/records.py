import json
import sqlite3

import numpy as np

from app import embeddings
from app.config import CUSTOMER_DEMO_PASSWORD
from app.db import hash_password
from app.pii import scan_text, try_engine


def lab_flag(value: float, low: float | None, high: float | None) -> str:
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"


def reference_phrase(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g}-{high:g}"
    if high is not None:
        return f"below {high:g}"
    if low is not None:
        return f"above {low:g}"
    return "not set"


def profile_text(client: dict) -> str:
    return (
        f"Client {client['full_name']}, MRN {client['mrn']}, born {client['date_of_birth']}. "
        f"Phone {client['phone']}. Email {client['email']}. Address {client['address']}."
    )


def appointment_text(client: dict, visit: dict) -> str:
    return (
        f"Client {client['full_name']}, MRN {client['mrn']}. "
        f"Appointment on {visit['appointment_date']} with {visit['clinician_name']}. "
        f"Reason: {visit['reason']}. Diagnosis: {visit['diagnosis']}. Note: {visit['note']}."
    )


def bloodwork_text(client: dict, lab: dict) -> str:
    bounds = reference_phrase(lab.get("ref_low"), lab.get("ref_high"))
    return (
        f"Client {client['full_name']}, MRN {client['mrn']}. "
        f"Bloodwork collected {lab['collected_at']}. Panel {lab['panel_name']}. "
        f"{lab['analyte']}: {lab['value']:g} {lab['unit']} "
        f"(reference {bounds}), flag {lab['flag']}."
    )


def next_mrn(conn: sqlite3.Connection) -> str:
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    rows = conn.execute("SELECT mrn FROM clients").fetchall()
    numbers = []
    for row in rows:
        mrn = row["mrn"]
        if mrn.startswith("MRN-") and mrn[4:].isdigit():
            numbers.append(int(mrn[4:]))
    return f"MRN-{max(numbers, default=10000) + 1}"


def public_client(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "mrn": row["mrn"],
        "full_name": row["full_name"],
        "date_of_birth": row["date_of_birth"],
        "email": row["email"],
        "phone": row["phone"],
        "address": row["address"],
        "synthetic": row["synthetic"],
    }


def require_client(conn: sqlite3.Connection, client_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown client.")
    return row


def write_chunk(
    conn: sqlite3.Connection,
    *,
    client_id: int,
    source_table: str,
    source_id: int,
    label: str,
    text: str,
    spans: list[dict],
    vector: np.ndarray | None = None,
) -> None:
    if vector is None:
        vector = embeddings.embed_texts([text])[0]
    conn.execute(
        """INSERT INTO chunks (client_id, source_table, source_id, label, text, pii_spans)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_table, source_id) DO UPDATE SET
             client_id = excluded.client_id,
             label = excluded.label,
             text = excluded.text,
             pii_spans = excluded.pii_spans""",
        (client_id, source_table, source_id, label, text, json.dumps(spans)),
    )
    chunk_id = conn.execute(
        "SELECT id FROM chunks WHERE source_table = ? AND source_id = ?",
        (source_table, source_id),
    ).fetchone()["id"]
    conn.execute(
        """INSERT INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)
           ON CONFLICT(chunk_id) DO UPDATE SET embedding = excluded.embedding""",
        (chunk_id, embeddings.pack_vector(vector)),
    )


def create_client(
    conn: sqlite3.Connection,
    *,
    full_name: str,
    date_of_birth: str,
    email: str,
    phone: str,
    address: str,
    synthetic: int = 0,
    password: str = CUSTOMER_DEMO_PASSWORD,
    mrn: str | None = None,
    vector: np.ndarray | None = None,
    spans: list[dict] | None = None,
) -> dict:
    mrn = mrn or next_mrn(conn)
    cursor = conn.execute(
        """INSERT INTO clients
           (mrn, full_name, date_of_birth, email, phone, address, password_hash, synthetic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (mrn, full_name, date_of_birth, email, phone, address, hash_password(password), synthetic),
    )
    client = {
        "id": cursor.lastrowid,
        "mrn": mrn,
        "full_name": full_name,
        "date_of_birth": date_of_birth,
        "email": email,
        "phone": phone,
        "address": address,
        "synthetic": synthetic,
    }
    text = profile_text(client)
    write_chunk(
        conn,
        client_id=client["id"],
        source_table="clients",
        source_id=client["id"],
        label=f"Profile · {mrn}",
        text=text,
        spans=scan_text(text) if spans is None else spans,
        vector=vector,
    )
    return client


def create_appointment(
    conn: sqlite3.Connection,
    *,
    client_id: int,
    appointment_date: str,
    clinician_name: str,
    reason: str,
    note: str,
    diagnosis: str,
    synthetic: int = 0,
    vector: np.ndarray | None = None,
    spans: list[dict] | None = None,
    engine=None,
) -> dict:
    client_row = require_client(conn, client_id)
    client = public_client(client_row)
    cursor = conn.execute(
        """INSERT INTO appointments
           (client_id, appointment_date, clinician_name, reason, note, diagnosis, synthetic)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (client_id, appointment_date, clinician_name, reason, note, diagnosis, synthetic),
    )
    visit = {
        "id": cursor.lastrowid,
        "client_id": client_id,
        "appointment_date": appointment_date,
        "clinician_name": clinician_name,
        "reason": reason,
        "note": note,
        "diagnosis": diagnosis,
        "synthetic": synthetic,
        "full_name": client["full_name"],
        "mrn": client["mrn"],
    }
    text = appointment_text(client, visit)
    if spans is None:
        own_engine = engine if engine is not None else try_engine()
        try:
            spans = scan_text(text, own_engine)
        finally:
            if engine is None:
                del own_engine
    write_chunk(
        conn,
        client_id=client_id,
        source_table="appointments",
        source_id=visit["id"],
        label=f"{appointment_date} · {reason}",
        text=text,
        spans=spans,
        vector=vector,
    )
    return visit


def create_bloodwork(
    conn: sqlite3.Connection,
    *,
    client_id: int,
    collected_at: str,
    panel_name: str,
    analyte: str,
    value: float,
    unit: str,
    ref_low: float | None,
    ref_high: float | None,
    appointment_id: int | None = None,
    synthetic: int = 0,
    vector: np.ndarray | None = None,
    spans: list[dict] | None = None,
) -> dict:
    client = public_client(require_client(conn, client_id))
    if appointment_id is not None:
        visit = conn.execute(
            "SELECT client_id FROM appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()
        if visit is None:
            raise ValueError("Unknown appointment.")
        if visit["client_id"] != client_id:
            raise ValueError("That appointment belongs to a different client.")
    flag = lab_flag(value, ref_low, ref_high)
    cursor = conn.execute(
        """INSERT INTO bloodwork
           (client_id, appointment_id, collected_at, panel_name, analyte, value, unit,
            ref_low, ref_high, flag, synthetic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            client_id,
            appointment_id,
            collected_at,
            panel_name,
            analyte,
            value,
            unit,
            ref_low,
            ref_high,
            flag,
            synthetic,
        ),
    )
    lab = {
        "id": cursor.lastrowid,
        "client_id": client_id,
        "appointment_id": appointment_id,
        "collected_at": collected_at,
        "panel_name": panel_name,
        "analyte": analyte,
        "value": value,
        "unit": unit,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "flag": flag,
        "synthetic": synthetic,
        "full_name": client["full_name"],
        "mrn": client["mrn"],
    }
    text = bloodwork_text(client, lab)
    write_chunk(
        conn,
        client_id=client_id,
        source_table="bloodwork",
        source_id=lab["id"],
        label=f"{analyte} · {collected_at}",
        text=text,
        spans=scan_text(text) if spans is None else spans,
        vector=vector,
    )
    return lab
