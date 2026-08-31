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
    if not exemplars:
        return ""
    blocks = []
    for ex in exemplars:
        blocks.append(
            f"<example>\n"
            f"Report: {ex.report_title} - {ex.report_description}\n"
            f"Model's initial (incorrect) guess: severity={ex.original_severity}, "
            f"component={ex.original_component}\n"
            f"Human correction: severity={ex.corrected_severity}, "
            f"component={ex.corrected_component}\n"
            f"Why the human corrected it: {ex.corrected_rationale}\n"
            f"</example>"
        )
    return (
        "Here are examples of how a human reviewer corrected similar past triage "
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
