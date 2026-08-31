import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from . import evaluation
from .config import EPOCH_CUTOFF
from .llm_client import BASE_RUBRIC
from .schemas import PromptVersion


class PromotionBlocked(Exception):
    def __init__(self, message: str, diff: dict):
        super().__init__(message)
        self.diff = diff


def _row_to_prompt_version(row: sqlite3.Row) -> PromptVersion:
    d = dict(row)
    d["is_active"] = bool(d["is_active"])
    return PromptVersion(**d)


def _now() -> str:
    """UTC timestamp matching schema.sql's `strftime('%Y-%m-%dT%H:%M:%fZ')`.

    Cutoffs are compared against `corrections.created_at` as TEXT, so the two
    must use identical formats. Python's %f is microseconds (6 digits) while
    SQLite's is milliseconds (3), and the mismatch made a correction written
    in the same millisecond as a cutoff sort *after* it and drop out of the
    exemplar set.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ensure_baseline(conn: sqlite3.Connection) -> PromptVersion:
    row = conn.execute("SELECT * FROM prompt_versions LIMIT 1").fetchone()
    if row is not None:
        return get_active(conn)

    conn.execute(
        """
        INSERT INTO prompt_versions
            (version_number, label, system_prompt, correction_cutoff, parent_id, is_active)
        VALUES (1, 'baseline (zero-shot)', ?, ?, NULL, 1)
        """,
        (BASE_RUBRIC, EPOCH_CUTOFF),
    )
    return get_active(conn)


def get_active(conn: sqlite3.Connection) -> PromptVersion:
    row = conn.execute(
        "SELECT * FROM prompt_versions WHERE is_active = 1 LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("No active prompt version. Run `triage init` first.")
    return _row_to_prompt_version(row)


def get_by_id(conn: sqlite3.Connection, version_id: int) -> PromptVersion:
    row = conn.execute(
        "SELECT * FROM prompt_versions WHERE id = ?", (version_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No prompt version with id {version_id}.")
    return _row_to_prompt_version(row)


def list_versions(conn: sqlite3.Connection) -> List[PromptVersion]:
    rows = conn.execute(
        "SELECT * FROM prompt_versions ORDER BY version_number"
    ).fetchall()
    return [_row_to_prompt_version(r) for r in rows]


def create_improved_version(
    conn: sqlite3.Connection, label: Optional[str] = None
) -> PromptVersion:
    active = get_active(conn)
    n_corrections = conn.execute(
        "SELECT COUNT(*) AS n FROM corrections WHERE created_at > ?",
        (active.correction_cutoff,),
    ).fetchone()["n"]
    if n_corrections == 0:
        raise RuntimeError(
            "No new corrections since the active prompt version's cutoff, "
            "nothing to improve from. Review some reports first with "
            "`triage review`."
        )

    next_version_number = (
        conn.execute("SELECT MAX(version_number) AS m FROM prompt_versions").fetchone()[
            "m"
        ]
        + 1
    )
    cutoff = _now()
    label = label or f"v{next_version_number} ({n_corrections} new corrections)"

    cur = conn.execute(
        """
        INSERT INTO prompt_versions
            (version_number, label, system_prompt, correction_cutoff, parent_id, is_active)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (next_version_number, label, active.system_prompt, cutoff, active.id),
    )
    return get_by_id(conn, cur.lastrowid)


def promote(
    conn: sqlite3.Connection, version_id: int, force: bool = False
) -> PromptVersion:
    candidate = get_by_id(conn, version_id)
    if candidate.is_active:
        return candidate

    active = get_active(conn)
    candidate_run = evaluation.get_latest_eval_run(conn, candidate.id)
    if candidate_run is None:
        raise RuntimeError(
            f"Prompt version {version_id} has no eval run yet. Run "
            f"`triage eval {version_id}` before promoting."
        )
    baseline_run = evaluation.get_latest_eval_run(conn, active.id)
    if baseline_run is None:
        raise RuntimeError(
            f"Active prompt version {active.id} has no eval run to compare "
            f"against. Run `triage eval {active.id}` first."
        )

    diff = evaluation.diff_runs(baseline_run, candidate_run)
    if diff["regressed"] and not force:
        raise PromotionBlocked(
            f"Refusing to promote: {len(diff['regressed'])} eval example(s) that "
            f"were correct under the active prompt version became incorrect "
            f"under version {version_id}. Inspect with `triage diff {active.id} "
            f"{version_id}`, then re-run with --force to promote anyway.",
            diff,
        )

    conn.execute("UPDATE prompt_versions SET is_active = 0 WHERE is_active = 1")
    conn.execute("UPDATE prompt_versions SET is_active = 1 WHERE id = ?", (version_id,))
    return get_by_id(conn, version_id)
