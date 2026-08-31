from html import escape

from .schemas import EvalRun, PromptVersion


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _row(label: str, entry: dict, css_class: str) -> str:
    before, after = entry["before"], entry["after"]
    return f"""
    <tr class="{css_class}">
      <td>{entry['report_id']}</td>
      <td>{escape(label)}</td>
      <td>{escape(before.predicted_severity)} / {escape(before.predicted_component)}</td>
      <td>{escape(after.predicted_severity)} / {escape(after.predicted_component)}</td>
    </tr>"""


def render_diff_html(
    baseline: PromptVersion,
    candidate: PromptVersion,
    baseline_run: EvalRun,
    candidate_run: EvalRun,
    diff: dict,
) -> str:
    rows = []
    for entry in diff["regressed"]:
        rows.append(_row("regressed", entry, "regressed"))
    for entry in diff["fixed"]:
        rows.append(_row("fixed", entry, "fixed"))
    for entry in diff["unchanged_incorrect"]:
        rows.append(_row("still incorrect", entry, "unchanged-bad"))
    for entry in diff["unchanged_correct"]:
        rows.append(_row("still correct", entry, "unchanged-good"))

    def delta_str(x: float) -> str:
        sign = "+" if x >= 0 else ""
        return f"{sign}{x * 100:.1f}pp"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Eval Diff: {escape(baseline.label or str(baseline.id))} vs {escape(candidate.label or str(candidate.id))}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 2rem auto; max-width: 900px; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .metrics {{ display: flex; gap: 1.5rem; margin: 1.5rem 0; }}
  .metric {{ background: #f5f5f5; border-radius: 8px; padding: 0.75rem 1rem; }}
  .metric .value {{ font-size: 1.3rem; font-weight: 600; }}
  .metric .label {{ font-size: 0.8rem; color: #555; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #eee;
            font-size: 0.9rem; }}
  th {{ background: #fafafa; }}
  tr.fixed {{ background: #e8f7ec; }}
  tr.regressed {{ background: #fdecec; }}
  tr.unchanged-bad {{ background: #fff8e6; }}
  .legend span {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px;
                  margin-right: 0.5rem; font-size: 0.8rem; }}
</style>
</head>
<body>
  <h1>Eval diff: "{escape(baseline.label or str(baseline.id))}" &rarr; "{escape(candidate.label or str(candidate.id))}"</h1>
  <p class="legend">
    <span style="background:#e8f7ec">fixed: {len(diff['fixed'])}</span>
    <span style="background:#fdecec">regressed: {len(diff['regressed'])}</span>
    <span style="background:#fff8e6">still incorrect: {len(diff['unchanged_incorrect'])}</span>
    <span>still correct: {len(diff['unchanged_correct'])}</span>
  </p>
  <div class="metrics">
    <div class="metric"><div class="value">{_pct(baseline_run.joint_accuracy)} &rarr; {_pct(candidate_run.joint_accuracy)}</div><div class="label">joint accuracy ({delta_str(diff['joint_accuracy_delta'])})</div></div>
    <div class="metric"><div class="value">{_pct(baseline_run.severity_accuracy)} &rarr; {_pct(candidate_run.severity_accuracy)}</div><div class="label">severity accuracy ({delta_str(diff['severity_accuracy_delta'])})</div></div>
    <div class="metric"><div class="value">{_pct(baseline_run.component_accuracy)} &rarr; {_pct(candidate_run.component_accuracy)}</div><div class="label">component accuracy ({delta_str(diff['component_accuracy_delta'])})</div></div>
  </div>
  <table>
    <thead><tr><th>Report</th><th>Outcome</th><th>Before (severity/component)</th><th>After (severity/component)</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>"""
