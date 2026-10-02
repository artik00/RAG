import sqlite3

from fastapi import HTTPException, Request, Response

from app.db import delete_session, get_session

COOKIE = "session"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 12,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def require_role(conn: sqlite3.Connection, request: Request, role: str) -> sqlite3.Row:
    session = get_session(conn, request.cookies.get(COOKIE))
    if session is None or session["role"] != role:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if role == "customer" and session["client_id"] is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return session


def logout(conn: sqlite3.Connection, request: Request, response: Response) -> None:
    delete_session(conn, request.cookies.get(COOKIE))
    clear_session_cookie(response)
