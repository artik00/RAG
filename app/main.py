from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.admin_api import router as admin_router
from app.customer_api import router as customer_router
from app.db import connect, init_db
from app.llm import ollama_status

STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Clinic chart", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(customer_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        counts = {
            name: conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
            for name in ("clients", "appointments", "bloodwork", "chunks")
        }
    return {**counts, "ollama": ollama_status()}
