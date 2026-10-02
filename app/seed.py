import argparse
import gc
import random
from datetime import date, timedelta

from app import embeddings
from app.db import connect, init_db
from app.pii import release_engine, scan_text, try_engine
from app.records import (
    appointment_text,
    bloodwork_text,
    create_appointment,
    create_bloodwork,
    create_client,
    lab_flag,
    profile_text,
)

CLIENT_COUNT = 100
APPOINTMENT_COUNT = 400
LAB_COUNT = 500
SEED = 42

FIRST = [
    "Avery", "Jordan", "Riley", "Quinn", "Morgan", "Casey", "Parker", "Reese",
    "Rowan", "Sage", "Emery", "Finley", "Harper", "Jules", "Kendall", "Logan",
    "Micah", "Noah", "Oakley", "Payton", "Remy", "Shiloh", "Tatum", "Val",
    "Winter", "Arden", "Blake", "Cameron", "Dakota", "Elliot", "Frankie", "Gray",
    "Haven", "Indigo", "Jesse", "Kit", "Lane", "Marlow", "Nico", "Ocean",
]
LAST = [
    "Chen", "Patel", "Nguyen", "Garcia", "Brooks", "Shah", "Cohen", "Diaz",
    "Hansen", "Ibarra", "Johal", "Khan", "Lewis", "Moreau", "Nwosu", "Okada",
    "Petrova", "Quintero", "Rahman", "Silva", "Tran", "Ueda", "Vogel", "Walsh",
    "Xu", "Yilmaz", "Zimmerman", "Adeyemi", "Berg", "Costa", "Duval", "Ellis",
    "Farah", "Gupta", "Horvat", "Iversen", "Jensen", "Kowalski", "Larsen", "Mensah",
]
CLINICIANS = [
    "Dr. Elena Vasquez", "Dr. Marcus Hale", "Dr. Priya Shah", "Dr. Owen Brooks",
    "Dr. Hannah Idris", "Dr. Leo Park", "Dr. Naomi Feldman", "Dr. Chris Adeyemi",
]
VISITS = [
    ("Annual physical", "Health maintenance", "Exam completed. No new complaints. Routine labs ordered."),
    ("Fatigue", "Fatigue, unspecified", "Symptoms for several weeks. Sleep is poor. Labs ordered."),
    ("Diabetes follow-up", "Type 2 diabetes mellitus", "Home glucose logs reviewed. Diet discussed. Repeat HbA1c."),
    ("Blood pressure check", "Essential hypertension", "Home readings reviewed. Continue current regimen."),
    ("Lipid review", "Hyperlipidemia", "Diet and prior lipid panel discussed. Repeat fasting lipids."),
    ("Joint pain", "Osteoarthritis", "Knee pain worse with stairs. Conservative care discussed."),
    ("Lab review", "Abnormal blood chemistry", "Prior results reviewed with the patient."),
    ("Cough", "Acute upper respiratory infection", "Symptoms for four days. Supportive care discussed."),
    ("Thyroid follow-up", "Hypothyroidism", "Energy and dose tolerance reviewed. TSH ordered."),
    ("Well visit", "General adult medical examination", "Preventive visit. Screening labs updated."),
]
ANALYTES = [
    ("CBC", "Hemoglobin", "g/dL", 12.0, 17.5, 1),
    ("CBC", "WBC", "10^3/uL", 4.0, 11.0, 1),
    ("CBC", "Platelets", "10^3/uL", 150, 450, 0),
    ("Metabolic", "Glucose", "mg/dL", 70, 99, 0),
    ("Metabolic", "Creatinine", "mg/dL", 0.6, 1.3, 2),
    ("Metabolic", "Potassium", "mmol/L", 3.5, 5.1, 1),
    ("Metabolic", "ALT", "U/L", 7, 56, 0),
    ("Lipids", "Total cholesterol", "mg/dL", 125, 199, 0),
    ("Lipids", "LDL", "mg/dL", 50, 99, 0),
    ("Lipids", "HDL", "mg/dL", 40, 90, 0),
    ("Lipids", "Triglycerides", "mg/dL", 40, 149, 0),
    ("Endocrine", "HbA1c", "%", 4.0, 5.6, 1),
    ("Endocrine", "TSH", "mIU/L", 0.4, 4.0, 2),
    ("Endocrine", "Vitamin D", "ng/mL", 30, 100, 1),
]


def _sample_value(rng: random.Random, low: float, high: float, places: int) -> float:
    outlier = rng.random() < 0.25
    span = high - low
    if outlier:
        if rng.random() < 0.5:
            value = low - span * rng.uniform(0.15, 0.45)
        else:
            value = high + span * rng.uniform(0.15, 0.7)
        value = max(value, 0.1)
    else:
        value = rng.uniform(low, high)
    return round(value, places)


def build_rows(rng: random.Random) -> tuple[list[dict], list[dict], list[dict]]:
    clients = []
    for index in range(CLIENT_COUNT):
        first = FIRST[index % len(FIRST)]
        block = index // len(FIRST)
        last = LAST[(index + block) % len(LAST)]
        mrn = f"MRN-{10001 + index}"
        year = 1946 + (index * 5) % 55
        clients.append(
            {
                "mrn": mrn,
                "full_name": f"{first} {last}",
                "date_of_birth": f"{year:04d}-{(index % 12) + 1:02d}-{(index % 27) + 1:02d}",
                "email": f"{first}.{last}.{10001 + index}@example.com".lower(),
                "phone": f"(415) 555-{100 + index:04d}",
                "address": f"{120 + index * 7} Cedar Lane, Springfield, IL {62701 + (index % 40)}",
            }
        )

    start = date(2023, 6, 1)
    appointments = []
    by_client: list[list[int]] = [[] for _ in clients]
    for index in range(APPOINTMENT_COUNT):
        client_index = rng.randrange(CLIENT_COUNT)
        reason, diagnosis, note = VISITS[index % len(VISITS)]
        client = clients[client_index]
        extra = ""
        if index % 3 == 0:
            extra = f" Patient confirmed phone {client['phone']}."
        if index % 11 == 0:
            extra += f" Insurance file uses SSN 900-{(index % 90) + 10:02d}-{(index % 9000) + 1000:04d}."
        visit_day = start + timedelta(days=rng.randrange(0, 800))
        appointments.append(
            {
                "client_index": client_index,
                "appointment_date": visit_day.isoformat(),
                "clinician_name": CLINICIANS[index % len(CLINICIANS)],
                "reason": reason,
                "diagnosis": diagnosis,
                "note": note + extra,
            }
        )
        by_client[client_index].append(index)

    labs = []
    for index in range(LAB_COUNT):
        client_index = rng.randrange(CLIENT_COUNT)
        panel, analyte, unit, low, high, places = ANALYTES[index % len(ANALYTES)]
        value = _sample_value(rng, low, high, places)
        linked = by_client[client_index]
        appointment_index = linked[rng.randrange(len(linked))] if linked and rng.random() < 0.8 else None
        if appointment_index is None:
            collected = (start + timedelta(days=rng.randrange(0, 800))).isoformat()
        else:
            collected = appointments[appointment_index]["appointment_date"]
        labs.append(
            {
                "client_index": client_index,
                "appointment_index": appointment_index,
                "collected_at": collected,
                "panel_name": panel,
                "analyte": analyte,
                "value": value,
                "unit": unit,
                "ref_low": low,
                "ref_high": high,
                "flag": lab_flag(value, low, high),
            }
        )
    return clients, appointments, labs


def _clear(conn) -> None:
    conn.execute("DELETE FROM chunk_embeddings")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM bloodwork")
    conn.execute("DELETE FROM appointments")
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM audit_log")
    conn.execute("DELETE FROM clients")


def seed(reset: bool = False) -> None:
    init_db()
    with connect() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM clients").fetchone()["n"]
    if existing and not reset:
        print(f"Database already has {existing} clients. Pass --reset to replace them.")
        return

    rng = random.Random(SEED)
    clients, appointments, labs = build_rows(rng)
    profile_chunks = [profile_text(client) for client in clients]
    visit_chunks = [
        appointment_text(clients[visit["client_index"]], visit) for visit in appointments
    ]
    lab_chunks = [bloodwork_text(clients[lab["client_index"]], lab) for lab in labs]
    texts = profile_chunks + visit_chunks + lab_chunks

    print(f"Scanning {len(visit_chunks)} appointment notes.")
    engine = try_engine()
    try:
        profile_spans = [scan_text(text) for text in profile_chunks]
        visit_spans = [scan_text(text, engine) for text in visit_chunks]
        lab_spans = [scan_text(text) for text in lab_chunks]
    finally:
        del engine
        release_engine()
        gc.collect()

    print(f"Embedding {len(texts)} chunks.")
    vectors = embeddings.embed_texts(texts)
    profile_vectors = vectors[:CLIENT_COUNT]
    visit_vectors = vectors[CLIENT_COUNT : CLIENT_COUNT + APPOINTMENT_COUNT]
    lab_vectors = vectors[CLIENT_COUNT + APPOINTMENT_COUNT :]

    with connect() as conn:
        if reset:
            _clear(conn)
        client_ids = []
        for client, vector, spans in zip(clients, profile_vectors, profile_spans):
            created = create_client(conn, synthetic=1, vector=vector, spans=spans, **client)
            client_ids.append(created["id"])
        appointment_ids = []
        for visit, vector, spans in zip(appointments, visit_vectors, visit_spans):
            created = create_appointment(
                conn,
                client_id=client_ids[visit["client_index"]],
                appointment_date=visit["appointment_date"],
                clinician_name=visit["clinician_name"],
                reason=visit["reason"],
                note=visit["note"],
                diagnosis=visit["diagnosis"],
                synthetic=1,
                vector=vector,
                spans=spans,
            )
            appointment_ids.append(created["id"])
        for lab, vector, spans in zip(labs, lab_vectors, lab_spans):
            appointment_id = (
                None
                if lab["appointment_index"] is None
                else appointment_ids[lab["appointment_index"]]
            )
            create_bloodwork(
                conn,
                client_id=client_ids[lab["client_index"]],
                appointment_id=appointment_id,
                collected_at=lab["collected_at"],
                panel_name=lab["panel_name"],
                analyte=lab["analyte"],
                value=lab["value"],
                unit=lab["unit"],
                ref_low=lab["ref_low"],
                ref_high=lab["ref_high"],
                synthetic=1,
                vector=vector,
                spans=spans,
            )
        counts = {
            name: conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
            for name in ("clients", "appointments", "bloodwork", "chunks")
        }
    print(
        f"Seeded {counts['clients']} clients, {counts['appointments']} appointments, "
        f"{counts['bloodwork']} bloodwork rows, {counts['chunks']} chunks."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Load synthetic clinic charts.")
    parser.add_argument("--reset", action="store_true", help="Replace existing rows")
    args = parser.parse_args()
    seed(reset=args.reset)


if __name__ == "__main__":
    main()
