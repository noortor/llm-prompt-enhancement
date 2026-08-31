import sqlite3
from typing import List, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .config import RETRIEVAL_MIN_SIMILARITY, RETRIEVAL_TOP_K
from .schemas import Exemplar


def get_exemplars(
    conn: sqlite3.Connection,
    query_title: str,
    query_description: str,
    correction_cutoff: str,
    exclude_report_id: Optional[int] = None,
    top_k: int = RETRIEVAL_TOP_K,
) -> List[Exemplar]:
    rows = conn.execute(
        """
        SELECT c.id AS correction_id, c.report_id, c.original_severity,
               c.original_component, c.corrected_severity, c.corrected_component,
               c.corrected_rationale, b.title AS report_title,
               b.description AS report_description
        FROM corrections c
        JOIN bug_reports b ON b.id = c.report_id
        WHERE c.created_at <= ?
        """,
        (correction_cutoff,),
    ).fetchall()

    candidates = [r for r in rows if r["report_id"] != exclude_report_id]
    if not candidates:
        return []

    candidate_texts = [f"{r['report_title']} {r['report_description']}" for r in candidates]
    query = f"{query_title} {query_description}"

    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(candidate_texts + [query])
    similarities = cosine_similarity(matrix[-1], matrix[:-1]).flatten()

    ranked = sorted(
        zip(candidates, similarities), key=lambda pair: pair[1], reverse=True
    )

    exemplars = []
    for row, sim in ranked[:top_k]:
        if sim < RETRIEVAL_MIN_SIMILARITY:
            continue
        exemplars.append(
            Exemplar(
                correction_id=row["correction_id"],
                report_title=row["report_title"],
                report_description=row["report_description"],
                original_severity=row["original_severity"],
                original_component=row["original_component"],
                corrected_severity=row["corrected_severity"],
                corrected_component=row["corrected_component"],
                corrected_rationale=row["corrected_rationale"],
                similarity=float(sim),
            )
        )
    return exemplars
