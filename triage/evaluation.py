import sqlite3
from typing import Dict, List, Optional

from . import retrieval
from .db import dumps
from .llm_client import triage_report
from .schemas import EvalResultRow, EvalRun, PromptVersion


def run_eval(conn: sqlite3.Connection, prompt_version: PromptVersion) -> EvalRun:
    reports = conn.execute(
        "SELECT * FROM bug_reports WHERE split = 'eval' ORDER BY id"
    ).fetchall()

    results: List[EvalResultRow] = []
    for report in reports:
        exemplars = retrieval.get_exemplars(
            conn,
            query_title=report["title"],
            query_description=report["description"],
            correction_cutoff=prompt_version.correction_cutoff,
            exclude_report_id=report["id"],
        )
        prediction = triage_report(
            title=report["title"],
            description=report["description"],
            system_prompt=prompt_version.system_prompt,
            exemplars=exemplars,
        )
        results.append(
            EvalResultRow(
                report_id=report["id"],
                predicted_severity=prediction.severity,
                predicted_component=prediction.component,
                predicted_rationale=prediction.rationale,
                severity_correct=prediction.severity == report["gold_severity"],
                component_correct=prediction.component == report["gold_component"],
            )
        )

    n = len(results)
    severity_acc = sum(r.severity_correct for r in results) / n if n else 0.0
    component_acc = sum(r.component_correct for r in results) / n if n else 0.0
    joint_acc = (
        sum(r.severity_correct and r.component_correct for r in results) / n
        if n
        else 0.0
    )

    cur = conn.execute(
        """
        INSERT INTO eval_runs
            (prompt_version_id, severity_accuracy, component_accuracy, joint_accuracy, n_examples)
        VALUES (?, ?, ?, ?, ?)
        """,
        (prompt_version.id, severity_acc, component_acc, joint_acc, n),
    )
    eval_run_id = cur.lastrowid

    for r in results:
        conn.execute(
            """
            INSERT INTO eval_results
                (eval_run_id, report_id, predicted_severity, predicted_component,
                 predicted_rationale, severity_correct, component_correct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eval_run_id,
                r.report_id,
                r.predicted_severity,
                r.predicted_component,
                r.predicted_rationale,
                int(r.severity_correct),
                int(r.component_correct),
            ),
        )

    row = conn.execute(
        "SELECT * FROM eval_runs WHERE id = ?", (eval_run_id,)
    ).fetchone()
    return EvalRun(**dict(row), results=results)


def get_latest_eval_run(
    conn: sqlite3.Connection, prompt_version_id: int
) -> Optional[EvalRun]:
    row = conn.execute(
        """
        SELECT * FROM eval_runs
        WHERE prompt_version_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (prompt_version_id,),
    ).fetchone()
    if row is None:
        return None
    result_rows = conn.execute(
        "SELECT * FROM eval_results WHERE eval_run_id = ? ORDER BY report_id",
        (row["id"],),
    ).fetchall()
    results = [
        EvalResultRow(
            report_id=r["report_id"],
            predicted_severity=r["predicted_severity"],
            predicted_component=r["predicted_component"],
            predicted_rationale=r["predicted_rationale"],
            severity_correct=bool(r["severity_correct"]),
            component_correct=bool(r["component_correct"]),
        )
        for r in result_rows
    ]
    return EvalRun(**dict(row), results=results)


def diff_runs(run_a: EvalRun, run_b: EvalRun) -> Dict:
    """Per-example diff of two eval runs (a = baseline, b = candidate).

    A report is "correct" for this diff when BOTH severity and component match
    gold. Buckets: fixed (a wrong -> b right), regressed (a right -> b wrong),
    unchanged_correct, unchanged_incorrect.
    """
    a_by_report = {r.report_id: r for r in run_a.results}
    b_by_report = {r.report_id: r for r in run_b.results}

    fixed, regressed, unchanged_correct, unchanged_incorrect = [], [], [], []

    for report_id in sorted(set(a_by_report) & set(b_by_report)):
        a = a_by_report[report_id]
        b = b_by_report[report_id]
        a_correct = a.severity_correct and a.component_correct
        b_correct = b.severity_correct and b.component_correct
        entry = {"report_id": report_id, "before": a, "after": b}
        if not a_correct and b_correct:
            fixed.append(entry)
        elif a_correct and not b_correct:
            regressed.append(entry)
        elif a_correct and b_correct:
            unchanged_correct.append(entry)
        else:
            unchanged_incorrect.append(entry)

    return {
        "fixed": fixed,
        "regressed": regressed,
        "unchanged_correct": unchanged_correct,
        "unchanged_incorrect": unchanged_incorrect,
        "severity_accuracy_delta": run_b.severity_accuracy - run_a.severity_accuracy,
        "component_accuracy_delta": run_b.component_accuracy - run_a.component_accuracy,
        "joint_accuracy_delta": run_b.joint_accuracy - run_a.joint_accuracy,
    }
