import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

CHAT_MODEL = os.environ.get("RAG_CHAT_MODEL", "qwen2.5:1.5b")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
CUSTOMER_DEMO_PASSWORD = "demo"
TOP_K = 3


def db_path() -> Path:
    override = os.environ.get("RAG_DB")
    if override:
        return Path(override)
    return DATA_DIR / "rag.db"
