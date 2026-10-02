import json
import urllib.error
import urllib.request

from app.config import CHAT_MODEL, OLLAMA_URL


class ModelUnavailable(Exception):
    pass


SYSTEM_CUSTOMER = (
    "You answer questions about a single patient's chart. "
    "Use only the passages. If the passages do not contain the answer, say you cannot find it in the chart. "
    "Cite the visit date or the lab analyte when you use a number. "
    "Do not invent measurements, diagnoses, or contact details. "
    "Keep the reply under 120 words."
)

SYSTEM_ADMIN = (
    "You answer questions about a clinic chart that may include many patients. "
    "Use only the passages. If they do not contain the answer, say you cannot find it in the chart. "
    "Name the patient and cite the visit date or lab analyte when you use a number. "
    "Do not invent measurements, diagnoses, or contact details. "
    "Keep the reply under 120 words."
)


def ollama_status() -> str:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=1.5) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        return "down"
    names = [model.get("name", "") for model in payload.get("models", [])]
    wanted = CHAT_MODEL.split(":")[0]
    if any(name == CHAT_MODEL or name.startswith(wanted + ":") for name in names):
        return "ready"
    return "model-missing"


def complete(question: str, passages: list[str], audience: str) -> str:
    system = SYSTEM_CUSTOMER if audience == "customer" else SYSTEM_ADMIN
    numbered = "\n\n".join(f"[{index}] {passage[:1500]}" for index, passage in enumerate(passages, start=1))
    payload = {
        "model": CHAT_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Passages:\n{numbered}\n\nQuestion: {question}"},
        ],
        "options": {
            "num_ctx": 1024,
            "num_predict": 256,
            "num_thread": 4,
            "temperature": 0.1,
        },
    }
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ModelUnavailable(
            f"The local chat model failed ({exc.code}). {detail} "
            f"Install it with: ollama pull {CHAT_MODEL}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ModelUnavailable(
            "The local chat model is not running. Start Ollama, then run: "
            f"ollama pull {CHAT_MODEL}"
        ) from exc
    message = body.get("message", {}).get("content", "").strip()
    if not message:
        raise ModelUnavailable("The local chat model returned an empty reply.")
    return message
