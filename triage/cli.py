from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from . import evaluation, prompt_versions, retrieval, seed_data
from .config import COMPONENTS, REVIEWER_EMAIL, SEVERITY_LEVELS
from .db import db_exists, dumps, get_conn, init_db
from .llm_client import triage_report

app = typer.Typer(help="Human-in-the-loop bug-triage improvement CLI.")
report_app = typer.Typer(help="Export static HTML reports.")
app.add_typer(report_app, name="report")

console = Console()


def _ensure_ready():
    if not db_exists():
        init_db()
    with get_conn() as conn:
        n_seeded = seed_data.seed(conn)
        prompt_versions.ensure_baseline(conn)
    if n_seeded:
        console.print(f"[dim]Seeded {n_seeded} bug reports.[/dim]")


@app.command()
def init():
    """Initialize the database, seed data, and baseline prompt version (idempotent)."""
    _ensure_ready()
    with get_conn() as conn:
        active = prompt_versions.get_active(conn)
    console.print(f"[green]Ready.[/green] Active prompt version: {active.label} (id={active.id})")


@app.command()
def status():
    """Show review progress, correction counts, and the active prompt version."""
    _ensure_ready()
    with get_conn() as conn:
        active = prompt_versions.get_active(conn)
        train_total = conn.execute(
            "SELECT COUNT(*) AS n FROM bug_reports WHERE split='train'"
        ).fetchone()["n"]
        reviewed = conn.execute(
            "SELECT COUNT(*) AS n FROM model_outputs WHERE prompt_version_id=?",
            (active.id,),
        ).fetchone()["n"]
        pending_corrections = conn.execute(
            "SELECT COUNT(*) AS n FROM corrections WHERE created_at > ?",
            (active.correction_cutoff,),
        ).fetchone()["n"]
        eval_total = conn.execute(
            "SELECT COUNT(*) AS n FROM bug_reports WHERE split='eval'"
        ).fetchone()["n"]
        latest_run = evaluation.get_latest_eval_run(conn, active.id)

    console.print(f"[bold]Active prompt version:[/bold] {active.label} (id={active.id})")
    console.print(f"[bold]Train reviewed:[/bold] {reviewed}/{train_total}")
    console.print(
        f"[bold]New corrections since active version's cutoff:[/bold] "
        f"{pending_corrections} (run `triage improve` once this is > 0)"
    )
    console.print(f"[bold]Eval set size:[/bold] {eval_total}")
    if latest_run:
        console.print(
            f"[bold]Active version's last eval:[/bold] joint="
            f"{latest_run.joint_accuracy:.1%}, severity={latest_run.severity_accuracy:.1%}, "
            f"component={latest_run.component_accuracy:.1%}"
        )
    else:
        console.print("[dim]Active version has not been evaluated yet.[/dim]")


@app.command()
def review(
    reviewer: str = typer.Option(
        REVIEWER_EMAIL, "--reviewer", help="Email recorded against any corrections you make."
    ),
):
    """Interactively triage+correct train-split reports under the active prompt version."""
    _ensure_ready()
    with get_conn() as conn:
        active = prompt_versions.get_active(conn)
        pending = conn.execute(
            """
            SELECT b.* FROM bug_reports b
            WHERE b.split = 'train'
              AND NOT EXISTS (
                SELECT 1 FROM model_outputs o
                WHERE o.report_id = b.id AND o.prompt_version_id = ?
              )
            ORDER BY b.id
            """,
            (active.id,),
        ).fetchall()

    if not pending:
        console.print("[green]Nothing pending[/green]: all train reports have been reviewed under the active prompt version.")
        return

    console.print(f"[bold]{len(pending)} report(s) to review under[/bold] {active.label}\n")
    reviewed_count, corrected_count = 0, 0

    try:
        for report in pending:
            with get_conn() as conn:
                exemplars = retrieval.get_exemplars(
                    conn,
                    query_title=report["title"],
                    query_description=report["description"],
                    correction_cutoff=active.correction_cutoff,
                    exclude_report_id=report["id"],
                )
                prediction = triage_report(
                    title=report["title"],
                    description=report["description"],
                    system_prompt=active.system_prompt,
                    exemplars=exemplars,
                )
                cur = conn.execute(
                    """
                    INSERT INTO model_outputs
                        (report_id, prompt_version_id, severity, component, rationale, exemplar_correction_ids)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report["id"],
                        active.id,
                        prediction.severity,
                        prediction.component,
                        prediction.rationale,
                        dumps([e.correction_id for e in exemplars]),
                    ),
                )
                output_id = cur.lastrowid

            console.print(
                Panel(
                    f"[bold]{report['title']}[/bold]\n{report['description']}",
                    title=f"Report #{report['id']}",
                )
            )
            if exemplars:
                console.print(
                    f"[dim]Retrieved {len(exemplars)} similar past correction(s) as examples "
                    f"(top similarity {exemplars[0].similarity:.2f}).[/dim]"
                )
            console.print(
                f"Model guess: [cyan]{prediction.severity}[/cyan] / "
                f"[cyan]{prediction.component}[/cyan]\n"
                f"Rationale: {prediction.rationale}"
            )

            severity = Prompt.ask(
                "Severity", choices=SEVERITY_LEVELS, default=prediction.severity
            )
            component = Prompt.ask(
                "Component", choices=COMPONENTS, default=prediction.component
            )

            changed = (
                severity != prediction.severity
                or component != prediction.component
            )

            # Only ask for reasoning when a label actually changed, and ask for
            # it fresh rather than pre-filled: a pre-filled rationale gets
            # accepted verbatim, which stores the model's justification for the
            # answer that was just overruled. Blank means "no explanation
            # given", recorded by keeping the model's text so downstream
            # rendering can tell the two cases apart.
            rationale = prediction.rationale
            if changed:
                written = Prompt.ask(
                    "Why is that the right call? (Enter to skip)", default=""
                ).strip()
                if written:
                    rationale = written

            reviewed_count += 1
            if changed:
                with get_conn() as conn:
                    conn.execute(
                        """
                        INSERT INTO corrections
                            (output_id, report_id, original_severity, original_component,
                             original_rationale, corrected_severity, corrected_component,
                             corrected_rationale, reviewer)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            output_id,
                            report["id"],
                            prediction.severity,
                            prediction.component,
                            prediction.rationale,
                            severity,
                            component,
                            rationale,
                            reviewer,
                        ),
                    )
                corrected_count += 1
                console.print("[yellow]Correction recorded.[/yellow]\n")
            else:
                console.print("[green]Approved as-is.[/green]\n")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped early.[/dim]")

    console.print(
        f"\n[bold]Session summary:[/bold] reviewed {reviewed_count}, "
        f"corrected {corrected_count}."
    )


@app.command()
def improve(label: Optional[str] = typer.Option(None, help="Optional label for the new version.")):
    """Create a new prompt version from corrections made since the active cutoff."""
    _ensure_ready()
    with get_conn() as conn:
        try:
            new_version = prompt_versions.create_improved_version(conn, label=label)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    console.print(
        f"[green]Created[/green] prompt version {new_version.id} "
        f"({new_version.label}). Not active yet, run `triage eval {new_version.id}` "
        f"then `triage promote {new_version.id}`."
    )


@app.command(name="eval")
def eval_cmd(version_id: Optional[int] = typer.Argument(None, help="Defaults to the active version.")):
    """Run the held-out eval set through a prompt version."""
    _ensure_ready()
    with get_conn() as conn:
        pv = (
            prompt_versions.get_active(conn)
            if version_id is None
            else prompt_versions.get_by_id(conn, version_id)
        )
        console.print(f"Running eval for version {pv.id} ({pv.label})...")
        run = evaluation.run_eval(conn, pv)

    table = Table(title=f"Eval results: version {pv.id}")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("n examples", str(run.n_examples))
    table.add_row("Severity accuracy", f"{run.severity_accuracy:.1%}")
    table.add_row("Component accuracy", f"{run.component_accuracy:.1%}")
    table.add_row("Joint accuracy", f"{run.joint_accuracy:.1%}")
    console.print(table)


@app.command()
def diff(baseline_id: int, candidate_id: int):
    """Diff two prompt versions' latest eval runs: fixes vs. regressions."""
    _ensure_ready()
    with get_conn() as conn:
        baseline = prompt_versions.get_by_id(conn, baseline_id)
        candidate = prompt_versions.get_by_id(conn, candidate_id)
        baseline_run = evaluation.get_latest_eval_run(conn, baseline_id)
        candidate_run = evaluation.get_latest_eval_run(conn, candidate_id)
        if baseline_run is None:
            console.print(f"Running eval for version {baseline_id} first...")
            baseline_run = evaluation.run_eval(conn, baseline)
        if candidate_run is None:
            console.print(f"Running eval for version {candidate_id} first...")
            candidate_run = evaluation.run_eval(conn, candidate)
        result = evaluation.diff_runs(baseline_run, candidate_run)

    def pp(x):
        sign = "+" if x >= 0 else ""
        return f"{sign}{x:.1%}"

    console.print(
        f"[bold]{baseline.label}[/bold] -> [bold]{candidate.label}[/bold]  "
        f"joint {pp(result['joint_accuracy_delta'])}  "
        f"severity {pp(result['severity_accuracy_delta'])}  "
        f"component {pp(result['component_accuracy_delta'])}"
    )
    console.print(f"[green]fixed: {len(result['fixed'])}[/green]  "
                  f"[red]regressed: {len(result['regressed'])}[/red]  "
                  f"unchanged-correct: {len(result['unchanged_correct'])}  "
                  f"unchanged-incorrect: {len(result['unchanged_incorrect'])}")

    if result["regressed"]:
        table = Table(title="Regressions (was correct, now wrong)")
        table.add_column("Report ID")
        table.add_column("Before")
        table.add_column("After")
        for entry in result["regressed"]:
            b, a = entry["before"], entry["after"]
            table.add_row(
                str(entry["report_id"]),
                f"{b.predicted_severity}/{b.predicted_component}",
                f"{a.predicted_severity}/{a.predicted_component}",
            )
        console.print(table)

    if result["fixed"]:
        table = Table(title="Fixed (was wrong, now correct)")
        table.add_column("Report ID")
        table.add_column("Before")
        table.add_column("After")
        for entry in result["fixed"]:
            b, a = entry["before"], entry["after"]
            table.add_row(
                str(entry["report_id"]),
                f"{b.predicted_severity}/{b.predicted_component}",
                f"{a.predicted_severity}/{a.predicted_component}",
            )
        console.print(table)


@app.command()
def promote(version_id: int, force: bool = typer.Option(False, "--force", help="Promote even if there are regressions.")):
    """Promote a prompt version to active, gated on eval evidence."""
    _ensure_ready()
    with get_conn() as conn:
        try:
            new_active = prompt_versions.promote(conn, version_id, force=force)
        except prompt_versions.PromotionBlocked as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    console.print(f"[green]Promoted[/green] version {new_active.id} ({new_active.label}) to active.")


@app.command()
def versions():
    """List all prompt versions with their latest eval accuracy, if any."""
    _ensure_ready()
    table = Table(title="Prompt versions")
    table.add_column("ID")
    table.add_column("Version")
    table.add_column("Label")
    table.add_column("Active")
    table.add_column("Joint acc.")
    with get_conn() as conn:
        for pv in prompt_versions.list_versions(conn):
            run = evaluation.get_latest_eval_run(conn, pv.id)
            table.add_row(
                str(pv.id),
                str(pv.version_number),
                pv.label or "",
                "✅" if pv.is_active else "",
                f"{run.joint_accuracy:.1%}" if run else "n/a",
            )
    console.print(table)


@app.command(name="try")
def try_report(
    title: str = typer.Option(..., prompt=True),
    description: str = typer.Option(..., prompt=True),
):
    """Ad hoc: triage an arbitrary report under the active prompt version (not persisted)."""
    _ensure_ready()
    with get_conn() as conn:
        active = prompt_versions.get_active(conn)
        exemplars = retrieval.get_exemplars(
            conn,
            query_title=title,
            query_description=description,
            correction_cutoff=active.correction_cutoff,
        )
    prediction = triage_report(
        title=title, description=description, system_prompt=active.system_prompt,
        exemplars=exemplars,
    )
    console.print(f"[cyan]{prediction.severity}[/cyan] / [cyan]{prediction.component}[/cyan]")
    console.print(prediction.rationale)
    if exemplars:
        console.print(f"[dim]({len(exemplars)} exemplar(s) used)[/dim]")


@report_app.command("export")
def report_export_cmd(
    baseline_id: int = typer.Option(..., "--baseline"),
    candidate_id: int = typer.Option(..., "--candidate"),
    out: str = typer.Option("eval_report.html", "--out"),
):
    """Export a static HTML fix/regression diff report for two prompt versions."""
    from .report_export import render_diff_html

    _ensure_ready()
    with get_conn() as conn:
        baseline = prompt_versions.get_by_id(conn, baseline_id)
        candidate = prompt_versions.get_by_id(conn, candidate_id)
        baseline_run = evaluation.get_latest_eval_run(conn, baseline_id)
        candidate_run = evaluation.get_latest_eval_run(conn, candidate_id)
        if baseline_run is None:
            baseline_run = evaluation.run_eval(conn, baseline)
        if candidate_run is None:
            candidate_run = evaluation.run_eval(conn, candidate)
        diff_result = evaluation.diff_runs(baseline_run, candidate_run)

    html = render_diff_html(baseline, candidate, baseline_run, candidate_run, diff_result)
    with open(out, "w") as f:
        f.write(html)
    console.print(f"[green]Wrote {out}[/green], open it in a browser to view.")


if __name__ == "__main__":
    app()
