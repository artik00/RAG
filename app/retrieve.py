import numpy as np

from app import embeddings
from app.config import TOP_K


def search(conn, question: str, client_id: int | None, k: int = TOP_K) -> list[dict]:
    query_vec = np.asarray(embeddings.embed_texts([question])[0], dtype=np.float32)
    if client_id is None:
        rows = conn.execute(
            """SELECT c.id, c.client_id, c.source_table, c.source_id, c.label, c.text, e.embedding
               FROM chunks c
               JOIN chunk_embeddings e ON e.chunk_id = c.id"""
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT c.id, c.client_id, c.source_table, c.source_id, c.label, c.text, e.embedding
               FROM chunks c
               JOIN chunk_embeddings e ON e.chunk_id = c.id
               WHERE c.client_id = ?""",
            (client_id,),
        ).fetchall()

    query_norm = float(np.linalg.norm(query_vec))
    scored: list[tuple[float, dict]] = []
    for row in rows:
        vector = np.frombuffer(row["embedding"], dtype=np.float32).astype(np.float32)
        if vector.shape != query_vec.shape:
            continue
        denom = query_norm * float(np.linalg.norm(vector))
        score = float(np.dot(query_vec, vector) / denom) if denom else 0.0
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)

    hits = []
    for score, row in scored[:k]:
        hits.append(
            {
                "id": row["id"],
                "client_id": row["client_id"],
                "source_table": row["source_table"],
                "source_id": row["source_id"],
                "label": row["label"],
                "text": row["text"],
                "score": score,
            }
        )
    return hits
