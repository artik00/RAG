import os
import re

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_engine = None
_engine_failed = False
_analyze_warned = False


def pattern_spans(text: str) -> list[dict]:
    found: list[dict] = []
    for kind, pattern in (("EMAIL", EMAIL_RE), ("PHONE", PHONE_RE), ("SSN", SSN_RE)):
        for match in pattern.finditer(text):
            found.append({"type": kind, "start": match.start(), "end": match.end()})
    return found


def presidio_spans(text: str, engine) -> list[dict]:
    global _analyze_warned
    if engine is None or not text:
        return []
    try:
        results = engine.analyze(text=text, language="en")
    except Exception as exc:
        if not _analyze_warned:
            _analyze_warned = True
            print(f"Presidio scan failed ({exc}). Using pattern scan only.")
        return []
    return [
        {"type": result.entity_type, "start": result.start, "end": result.end}
        for result in results
    ]


def merge_spans(*groups: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    merged: list[dict] = []
    for group in groups:
        for span in group:
            key = (span["type"], span["start"], span["end"])
            if key in seen:
                continue
            seen.add(key)
            merged.append({"type": span["type"], "start": span["start"], "end": span["end"]})
    return merged


def try_engine():
    global _engine, _engine_failed
    if os.environ.get("RAG_DISABLE_PRESIDIO") == "1" or _engine_failed:
        return None
    if _engine is not None:
        return _engine
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        provider = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
            }
        )
        _engine = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        return _engine
    except Exception as exc:
        _engine_failed = True
        print(f"Presidio is not available ({exc}). Notes will be scanned with patterns only.")
        return None


def release_engine() -> None:
    global _engine
    _engine = None


def scan_text(text: str, engine=None) -> list[dict]:
    return merge_spans(pattern_spans(text), presidio_spans(text, engine))
