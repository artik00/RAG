from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import ask as ask_service
from app.auth import clear_session_cookie, logout, require_role, set_session_cookie
from app.db import audit, connect, create_session, verify_password
from app.llm import ModelUnavailable
from app.records import public_client

router = APIRouter(prefix="/customer/v1", tags=["customer"])


class CustomerLogin(BaseModel):
    mrn: str = Field(min_length=1, max_length=40)
    password: str


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@router.post("/login")
def login(body: CustomerLogin, response: Response) -> dict:
    mrn = body.mrn.strip().upper()
    with connect() as conn:
        row = conn.execute("SELECT * FROM clients WHERE mrn = ?", (mrn,)).fetchone()
        if row is None or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="Unknown MRN or password.")
        token = create_session(conn, "customer", row["id"])
        audit(conn, "customer", row["id"], "login", mrn)
        client = public_client(row)
    set_session_cookie(response, token)
    return {"role": "customer", "client": client}


@router.post("/logout")
def sign_out(request: Request, response: Response) -> dict:
    with connect() as conn:
        logout(conn, request, response)
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    with connect() as conn:
        session = require_role(conn, request, "customer")
        row = conn.execute("SELECT * FROM clients WHERE id = ?", (session["client_id"],)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Sign in required.")
        return {"role": "customer", "client": public_client(row)}


@router.get("/chart")
def chart(request: Request) -> dict:
    with connect() as conn:
        session = require_role(conn, request, "customer")
        client_id = session["client_id"]
        client = conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
        if client is None:
            raise HTTPException(status_code=401, detail="Sign in required.")
        visits = conn.execute(
            """SELECT id, appointment_date, clinician_name, reason, note, diagnosis
               FROM appointments WHERE client_id = ?
               ORDER BY appointment_date DESC""",
            (client_id,),
        ).fetchall()
        labs = conn.execute(
            """SELECT id, collected_at, panel_name, analyte, value, unit, ref_low, ref_high, flag
               FROM bloodwork WHERE client_id = ?
               ORDER BY collected_at DESC, analyte""",
            (client_id,),
        ).fetchall()
        audit(conn, "customer", client_id, "read_chart", client["mrn"])
        return {
            "client": public_client(client),
            "appointments": [dict(row) for row in visits],
            "bloodwork": [dict(row) for row in labs],
        }


@router.post("/query")
def query(body: QueryIn, request: Request) -> dict:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Enter a question.")
    try:
        with connect() as conn:
            session = require_role(conn, request, "customer")
            return ask_service.ask(conn, question, client_id=session["client_id"])
    except ModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
