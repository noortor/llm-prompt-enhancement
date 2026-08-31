# Human-in-the-Loop Bug Triage

A CLI tool that captures human corrections to an LLM's bug-triage output and turns
them into a measurable, evidence-gated improvement, without fine-tuning.

An LLM classifies bug reports into `severity` + `component` + `rationale`. A
reviewer corrects it where it's wrong. Those corrections become retrievable
few-shot examples for future triage calls. Every new "prompt version" is
scored against a held-out eval set before it's allowed to go live, and
promotion is blocked if it would silently regress any previously-correct
example.

See [`DESIGN.md`](DESIGN.md) for the full design writeup (approach, tradeoffs,
limitations, what I'd do with more time).

## Setup

Requires Python 3.9+ and an Anthropic API key.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and paste your ANTHROPIC_API_KEY
```

Get a key at console.anthropic.com (Settings → API Keys); billing must be
enabled on the account. The default model (`claude-haiku-4-5-20251001`) is
cheap: the full walkthrough below costs well under $1.

All commands below assume the venv is active. You can also use the wrapper
`./triage.sh <command>` instead of `python -m triage.cli <command>` to skip
activating the venv yourself.

## Quickstart: run the full loop

```bash
# 1. Initialize the DB, seed 40 synthetic bug reports (27 train / 13 eval),
#    and create the baseline prompt version (v1, zero-shot, no exemplars).
python -m triage.cli init

# 2. Score the baseline against the held-out eval set BEFORE any human input.
python -m triage.cli eval 1

# 3. Review the 27 train-split reports interactively. For each one you'll see
#    the model's guess, then a severity and a component prompt pre-filled
#    with its answer; press Enter to approve, or type a new value. If you
#    change either one, you're asked why (optional, Enter to skip).
python -m triage.cli review

# 4. Build a new prompt version from whatever corrections you just made.
python -m triage.cli improve

# 5. Score the new version against the SAME held-out eval set.
python -m triage.cli eval 2

# 6. See exactly what changed, example by example.
python -m triage.cli diff 1 2

# 7. Try to promote it live. This is BLOCKED if any previously-correct eval
#    example became incorrect (a regression); that's the trust gate.
python -m triage.cli promote 2
# If blocked and you've inspected the diff and still want it live:
python -m triage.cli promote 2 --force

# 8. Export a static HTML report of the before/after diff.
python -m triage.cli report export --baseline 1 --candidate 2 --out eval_report.html
open eval_report.html   # (or just open the file in a browser)
```

Your exact numbers will depend on which corrections you make, since the whole
point is that human judgment drives the improvement. In the run documented in
`DESIGN.md`, 13 corrections took severity accuracy from **38.5% to 84.6%** on
the held-out set (component accuracy holds at 100% throughout), fixing 8
examples and regressing 2. Those regressions are real and reproducible: the
promotion gate blocks on them and requires an explicit `--force` to override.

## All commands

| Command | What it does |
|---|---|
| `triage init` | Create the DB, seed data, and baseline prompt version. Safe to re-run (no-ops if already set up). |
| `triage status` | Review progress, pending corrections, active prompt version, last eval scores. |
| `triage review [--reviewer EMAIL]` | Interactively triage + correct all not-yet-reviewed train reports under the active prompt version. |
| `triage improve [--label TEXT]` | Create a new prompt version from corrections made since the active version's cutoff. |
| `triage eval [VERSION_ID]` | Run the held-out eval set through a prompt version (defaults to active). |
| `triage diff BASELINE_ID CANDIDATE_ID` | Per-example fix/regression diff between two versions' latest eval runs. |
| `triage promote VERSION_ID [--force]` | Make a version active. Blocked if it has any eval regressions vs. the current active version, unless `--force`. |
| `triage versions` | List all prompt versions with their latest eval accuracy. |
| `triage try --title T --description D` | Ad hoc triage of any text under the active prompt version. Nothing is persisted. |
| `triage report export --baseline A --candidate B --out FILE` | Static HTML diff report (no server needed). |

## How the loop works

- **No fine-tuning.** A "prompt version" is a system prompt plus a
  `correction_cutoff` timestamp; the underlying model never changes. What
  changes is which past corrections are eligible to be retrieved (TF-IDF
  cosine similarity over report text) and injected as few-shot examples at
  inference time.
- **Held-out evaluation.** 13 of the 40 seed reports are `eval`-split and
  never shown to the reviewer. Every prompt version is scored against the
  same 13 examples, so before/after comparisons are apples-to-apples.
- **Regression-gated promotion.** `triage promote` compares the candidate's
  latest eval run to the active version's, example by example. Any example
  that was correct and became incorrect blocks promotion unless you pass
  `--force`, a deliberate human override, not a silent auto-promote.

## Resetting

```bash
rm -f data/app.db
python -m triage.cli init
```
