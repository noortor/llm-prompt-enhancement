from typing import List

import anthropic

from .config import ANTHROPIC_API_KEY, COMPONENTS, SEVERITY_LEVELS, TRIAGE_MODEL
from .schemas import Exemplar, TriageOutput

BASE_RUBRIC = f"""You are a bug-triage assistant for an engineering team. Given a bug \
report, classify it using the `submit_triage` tool.

Severity levels (choose exactly one):
- Critical: system-wide outage, data loss, security breach, or a core workflow \
completely blocked with no workaround.
- High: a major feature is broken or badly degraded for many users; a workaround \
may exist but is costly.
- Medium: a real defect with noticeable but limited impact, or a reasonable \
workaround exists.
- Low: cosmetic, edge-case, or minor annoyance with negligible impact on users' \
ability to do their work.

Components (choose exactly one): {", ".join(COMPONENTS)}.

Write a one-to-two sentence rationale that references specific details from the \
report."""

TRIAGE_TOOL = {
    "name": "submit_triage",
    "description": "Submit the triage classification for a bug report.",
    "input_schema": {
        "type": "object",
        "properties": {
            "severity": {"type": "string", "enum": SEVERITY_LEVELS},
            "component": {"type": "string", "enum": COMPONENTS},
            "rationale": {"type": "string"},
        },
        "required": ["severity", "component", "rationale"],
    },
}


def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _format_exemplars(exemplars: List[Exemplar]) -> str:
    """Render past corrections as few-shot examples.

    Only fields the reviewer actually changed are shown as corrections; a
    field the reviewer left alone was already right, and labelling it wrong
    teaches the opposite of what the correction meant. The rationale is
    included only when the reviewer wrote their own, since `review`
    pre-fills it with the model's text (see Exemplar.reviewer_wrote_rationale).
    """
    if not exemplars:
        return ""
    blocks = []
    for ex in exemplars:
        lines = [
            "<example>",
            f"Report: {ex.report_title} - {ex.report_description}",
        ]

        changes = []
        if ex.corrected_severity != ex.original_severity:
            changes.append(
                f"severity: the model answered {ex.original_severity}, "
                f"the reviewer corrected it to {ex.corrected_severity}"
            )
        if ex.corrected_component != ex.original_component:
            changes.append(
                f"component: the model answered {ex.original_component}, "
                f"the reviewer corrected it to {ex.corrected_component}"
            )

        if changes:
            lines.append("The reviewer corrected:")
            lines.extend(f"- {c}" for c in changes)
        unchanged = []
        if ex.corrected_severity == ex.original_severity:
            unchanged.append(f"severity={ex.corrected_severity}")
        if ex.corrected_component == ex.original_component:
            unchanged.append(f"component={ex.corrected_component}")
        if unchanged:
            lines.append(
                "The reviewer confirmed as already correct: " + ", ".join(unchanged)
            )

        if ex.reviewer_wrote_rationale:
            lines.append(f"The reviewer's reasoning: {ex.corrected_rationale}")

        lines.append("</example>")
        blocks.append("\n".join(lines))

    return (
        "Here are examples of how a human reviewer judged similar past triage "
        "calls. Use these to calibrate your judgment on the new report below, but "
        "don't just copy their severity/component if this report is meaningfully "
        "different.\n\n" + "\n\n".join(blocks) + "\n\n"
    )


def triage_report(
    title: str, description: str, system_prompt: str, exemplars: List[Exemplar]
) -> TriageOutput:
    client = _client()
    exemplar_block = _format_exemplars(exemplars)
    user_message = (
        f"{exemplar_block}Now triage this new report:\n"
        f"Title: {title}\n"
        f"Description: {description}"
    )

    response = client.messages.create(
        model=TRIAGE_MODEL,
        max_tokens=512,
        temperature=0.0,
        system=system_prompt,
        tools=[TRIAGE_TOOL],
        tool_choice={"type": "tool", "name": "submit_triage"},
        messages=[{"role": "user", "content": user_message}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_triage":
            return TriageOutput(**block.input)

    raise RuntimeError("Model did not return a submit_triage tool call.")
