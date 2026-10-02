import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import ask as ask_service
from app.auth import clear_session_cookie, logout, require_role, set_session_cookie
from app.config import CUSTOMER_DEMO_PASSWORD
from app.db import audit, connect, create_session, verify_password
from app.llm import ModelUnavailable
from app.records import create_appointment, create_bloodwork, create_client

router = APIRouter(prefix="/admin/v1", tags=["admin"])


class AdminLogin(BaseModel):
    password: str


class ClientIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    date_of_birth: str
    email: str = Field(min_length=3, max_length=200)
    phone: str = Field(min_length=7, max_length=40)
    address: str = Field(min_length=1, max_length=200)


class AppointmentIn(BaseModel):
    client_id: int
    appointment_date: str
    clinician_name: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=200)
    note: str = Field(min_length=1, max_length=2000)
    diagnosis: str = Field(min_length=1, max_length=200)


class BloodworkIn(BaseModel):
    client_id: int
    appointment_id: int | None = None
    collected_at: str
    panel_name: str = Field(min_length=1, max_length=80)
    analyte: str = Field(min_length=1, max_length=80)
    value: float
    unit: str = Field(min_length=1, max_length=40)
    ref_low: float | None = None
    ref_high: float | None = None


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


def _day(value: str) -> str:
    try:
        datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Use a YYYY-MM-DD date.") from exc
    return value[:10]


def _like(q: str | None) -> str | None:
    if q is None or not q.strip():
        return None
    return f"%{q.strip()}%"


@router.post("/login")
def login(body: AdminLogin, response: Response) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'admin_password_hash'"
        ).fetchone()
        if row is None or not verify_password(body.password, row["value"]):
            raise HTTPException(status_code=401, detail="Incorrect password.")
        token = create_session(conn, "admin", None)
        audit(conn, "admin", None, "login", "admin sign-in")
    set_session_cookie(response, token)
    return {"role": "admin"}


@router.post("/logout")
def sign_out(request: Request, response: Response) -> dict:
    with connect() as conn:
        logout(conn, request, response)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    with connect() as conn:
        require_role(conn, request, "admin")
    return {"role": "admin"}


@router.get("/clients")
def clients(request: Request, q: str | None = None) -> list[dict]:
    like = _like(q)
    with connect() as conn:
        require_role(conn, request, "admin")
        if like is None:
            rows = conn.execute(
                """SELECT id, mrn, full_name, date_of_birth, email, phone, address, synthetic
                   FROM clients ORDER BY full_name"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, mrn, full_name, date_of_birth, email, phone, address, synthetic
                   FROM clients
                   WHERE full_name LIKE ? OR mrn LIKE ? OR email LIKE ? OR phone LIKE ?
                   ORDER BY full_name""",
                (like, like, like, like),
            ).fetchall()
        audit(conn, "admin", None, "list_clients", q or "")
    return [dict(row) for row in rows]


@router.get("/appointments")
def appointments(request: Request, q: str | None = None) -> list[dict]:
    like = _like(q)
    with connect() as conn:
        require_role(conn, request, "admin")
        sql = """SELECT a.id, a.client_id, a.appointment_date, a.clinician_name, a.reason,
                        a.note, a.diagnosis, a.synthetic, c.full_name, c.mrn
                 FROM appointments a
                 JOIN clients c ON c.id = a.client_id"""
        params: tuple = ()
        if like is not None:
            sql += " WHERE c.full_name LIKE ? OR c.mrn LIKE ? OR a.reason LIKE ? OR a.diagnosis LIKE ?"
            params = (like, like, like, like)
        sql += " ORDER BY a.appointment_date DESC"
        rows = conn.execute(sql, params).fetchall()
        audit(conn, "admin", None, "list_appointments", q or "")
    return [dict(row) for row in rows]


@router.get("/bloodwork")
def bloodwork(request: Request, q: str | None = None) -> list[dict]:
    like = _like(q)
    with connect() as conn:
        require_role(conn, request, "admin")
        sql = """SELECT b.id, b.client_id, b.appointment_id, b.collected_at, b.panel_name,
                        b.analyte, b.value, b.unit, b.ref_low, b.ref_high, b.flag, b.synthetic,
                        c.full_name, c.mrn
                 FROM bloodwork b
                 JOIN clients c ON c.id = b.client_id"""
        params = ()
        if like is not None:
            sql += " WHERE c.full_name LIKE ? OR c.mrn LIKE ? OR b.analyte LIKE ? OR b.panel_name LIKE ?"
            params = (like, like, like, like)
        sql += " ORDER BY b.collected_at DESC"
        rows = conn.execute(sql, params).fetchall()
        audit(conn, "admin", None, "list_bloodwork", q or "")
    return [dict(row) for row in rows]


def _create(fn):
    try:
        with connect() as conn:
            return fn(conn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="That email or MRN is already in the chart.") from exc
    except ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/clients", status_code=201)
def add_client(body: ClientIn, request: Request) -> dict:
    def run(conn):
        require_role(conn, request, "admin")
        client = create_client(
            conn,
            full_name=body.full_name.strip(),
            date_of_birth=_day(body.date_of_birth),
            email=body.email.strip(),
            phone=body.phone.strip(),
            address=body.address.strip(),
        )
        audit(conn, "admin", client["id"], "create_client", client["mrn"])
        client["password"] = CUSTOMER_DEMO_PASSWORD
        return client

    return _create(run)


@router.post("/appointments", status_code=201)
def add_appointment(body: AppointmentIn, request: Request) -> dict:
    def run(conn):
        require_role(conn, request, "admin")
        visit = create_appointment(
            conn,
            client_id=body.client_id,
            appointment_date=_day(body.appointment_date),
            clinician_name=body.clinician_name.strip(),
            reason=body.reason.strip(),
            note=body.note.strip(),
            diagnosis=body.diagnosis.strip(),
        )
        audit(conn, "admin", body.client_id, "create_appointment", str(visit["id"]))
        return visit

    return _create(run)


@router.post("/bloodwork", status_code=201)
def add_bloodwork(body: BloodworkIn, request: Request) -> dict:
    def run(conn):
        require_role(conn, request, "admin")
        lab = create_bloodwork(
            conn,
            client_id=body.client_id,
            appointment_id=body.appointment_id,
            collected_at=_day(body.collected_at),
            panel_name=body.panel_name.strip(),
            analyte=body.analyte.strip(),
            value=body.value,
            unit=body.unit.strip(),
            ref_low=body.ref_low,
            ref_high=body.ref_high,
        )
        audit(conn, "admin", body.client_id, "create_bloodwork", lab["analyte"])
        return lab

    return _create(run)


@router.post("/query")
def query(body: QueryIn, request: Request) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Enter a question.")
    try:
        with connect() as conn:
            require_role(conn, request, "admin")
            return ask_service.ask(conn, question, client_id=None)
    except ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
