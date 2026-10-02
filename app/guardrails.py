import json
import re

_CROSS = re.compile(
    r"\b(all|every)\s+(the\s+)?(patients?|clients?|charts?)\b"
    r"|\b(other|another)\s+(patient|client|person|chart)s?\b"
    r"|\bsomeone\s+else'?s?\b"
    r"|\b(everybody|everyone)\b"
    r"|\blist\s+(everyone|everybody|all\s+patients?|all\s+clients?)\b"
    r"|\bother\s+people\b",
    re.IGNORECASE,
)
_MRN = re.compile(r"\bMRN-\d+\b", re.IGNORECASE)

REFUSAL = "I can only answer questions about your own chart."

_SPAN_PLACEHOLDERS = {
    "PERSON": "[PERSON]",
    "NRP": "[PERSON]",
    "EMAIL": "[EMAIL]",
    "EMAIL_ADDRESS": "[EMAIL]",
    "PHONE": "[PHONE]",
    "PHONE_NUMBER": "[PHONE]",
    "SSN": "[SSN]",
    "US_SSN": "[SSN]",
    "LOCATION": "[ADDRESS]",
    "ADDRESS": "[ADDRESS]",
    "CREDIT_CARD": "[CREDIT_CARD]",
    "US_DRIVER_LICENSE": "[ID]",
    "MEDICAL_LICENSE": "[ID]",
    "IBAN_CODE": "[ID]",
    "IP_ADDRESS": "[ID]",
}


def values_in_spans(text: str, raw_spans: str) -> list[tuple[str, str]]:
    try:
        spans = json.loads(raw_spans or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(spans, list):
        return []
    found: list[tuple[str, str]] = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        start = span.get("start")
        end = span.get("end")
        placeholder = _SPAN_PLACEHOLDERS.get(str(span.get("type") or ""))
        if placeholder is None or not isinstance(start, int) or not isinstance(end, int):
            continue
        if not 0 <= start < end <= len(text):
            continue
        value = text[start:end].strip()
        if len(value) < 3:
            continue
        found.append((value, placeholder))
    return found


def targets_other_client(
    question: str,
    self_mrn: str,
    others: list[dict],
    secrets: list[tuple[str, str]] | None = None,
) -> bool:
    if _CROSS.search(question):
        return True
    for match in _MRN.finditer(question):
        if match.group(0).upper() != self_mrn.upper():
            return True
    folded = question.casefold()
    question_digits = re.sub(r"\D", "", question)
    for other in others:
        mrn = str(other.get("mrn") or "")
        if mrn and mrn.casefold() in folded:
            return True
        name = str(other.get("full_name") or "")
        if len(name) >= 5 and name.casefold() in folded:
            return True
        email = str(other.get("email") or "")
        if email and email.casefold() in folded:
            return True
        address = str(other.get("address") or "")
        if len(address) >= 8 and address.casefold() in folded:
            return True
        digits = re.sub(r"\D", "", str(other.get("phone") or ""))
        if len(digits) >= 10 and digits in question_digits:
            return True
    for value, _placeholder in secrets or []:
        if len(value) >= 5 and value.casefold() in folded:
            return True
    return False


def _phone_pattern(phone: str) -> re.Pattern[str] | None:
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    body = r"\D*".join(re.escape(digit) for digit in digits)
    return re.compile(rf"(?<!\d){body}(?!\d)")


def scrub(
    text: str,
    others: list[dict],
    secrets: list[tuple[str, str]] | None = None,
) -> str:
    replacements: list[tuple[str, str]] = []
    phones: list[re.Pattern[str]] = []
    for value, placeholder in secrets or []:
        replacements.append((value, placeholder))
    for other in others:
        replacements.append((str(other.get("address") or ""), "[ADDRESS]"))
        replacements.append((str(other.get("full_name") or ""), "[PERSON]"))
        replacements.append((str(other.get("email") or ""), "[EMAIL]"))
        replacements.append((str(other.get("phone") or ""), "[PHONE]"))
        replacements.append((str(other.get("mrn") or ""), "[MRN]"))
        pattern = _phone_pattern(str(other.get("phone") or ""))
        if pattern is not None:
            phones.append(pattern)
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    cleaned = text
    for raw, placeholder in replacements:
        if len(raw) < 3:
            continue
        cleaned = re.sub(re.escape(raw), placeholder, cleaned, flags=re.IGNORECASE)
    for pattern in phones:
        cleaned = pattern.sub("[PHONE]", cleaned)
    return cleaned
