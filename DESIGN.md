# Design Writeup

## Approach

The system is a CLI: a single Python package with a terminal interface for
reviewing outputs, versioning prompts, and running evals. The task is
bug-triage classification, one structured-output call per report, so the
pieces that need to exist are a data layer (SQLite), an LLM integration
layer, and an interaction layer for a human reviewer, and a terminal
interface covers all three directly for a tool built around one reviewer
working through reports at a time. A static HTML export command covers the
one place a browser adds something the terminal doesn't: visually scanning
a fix/regression diff.

Because the underlying triage LLM this tooling improves sits behind a
customer-facing product, even though the tooling itself is only ever used by
engineers, the eval design treats *any* individual regression as
disqualifying by default (see the regression gate below), rather than
trusting an aggregate accuracy improvement to mean "safe to ship."

The improvement mechanism is **retrieval-augmented few-shot prompting, not
fine-tuning**. A "prompt version" is `{system_prompt, correction_cutoff}`;
the model's weights never change. When a reviewer corrects an output, that
correction becomes a candidate exemplar. Creating a new prompt version just
advances the cutoff, making newly-collected corrections eligible to be
retrieved (TF-IDF cosine similarity over report text, top-k=3) and injected
as few-shot examples the next time a similar report is triaged. This is
fast, cheap, and fully auditable (every triage call's output records
exactly which past corrections it saw), at the cost of a lower ceiling than
an actual weight update (see Limitations).

Trustworthiness is enforced with a **held-out eval set and a regression
gate**, not just a diff report someone might skim past. 13 of the 40 seed
reports are `eval`-split and never shown to the reviewer. Every prompt
version gets scored against the same 13 examples, and `triage promote`
mechanically refuses to activate a candidate if any example that was correct
under the active version becomes incorrect under the candidate; a human can
override with `--force`, but only after seeing the specific regression, not
by default.

## Data Schema

Six tables, each corresponding to one stage of the loop:

**`bug_reports`** is the dataset. Each row is one report (`title`,
`description`), tagged `train` or `eval`, with a `gold_severity`/
`gold_component` set when the data was written. Gold labels are used only
for scoring `eval`-split rows and are never shown to the reviewer; on
`train`-split rows they exist for data-generation consistency rather than
being consumed anywhere.

**`prompt_versions`** is the versioned behavior of the triage model. Only
two fields actually affect that behavior: `system_prompt` (the fixed
instructions) and `correction_cutoff` (a timestamp gating which rows in
`corrections` are eligible to be retrieved as exemplars under this version).
The rest, `version_number`, `label`, `parent_id`, `is_active`, `created_at`,
is bookkeeping for versioning, lineage, and state, not anything that changes
model behavior.

**`model_outputs`** holds one row per (report, prompt version) triage call:
the predicted `severity`/`component`/`rationale`, plus
`exemplar_correction_ids` (a JSON list) recording exactly which past
corrections were shown for that call, so any output is auditable after the
fact.

**`corrections`** is the human edit itself: a report's original
(model-produced) fields next to the reviewer's corrected fields, tied back
to the specific `output_id` it corrects, with a `reviewer` and a timestamp.
This table is what the entire loop runs on, every row here is a candidate
exemplar for future prompt versions.

**`eval_runs`** holds one row per (prompt version × full pass over the eval
set): aggregate `severity_accuracy`/`component_accuracy`/`joint_accuracy`
and how many examples were scored.

**`eval_results`** holds one row per (eval run × individual eval report):
the prediction and whether severity/component matched gold for that
example. Joining two eval runs' `eval_results` on `report_id` is what makes
the fix/regression diff possible at the individual-example level, rather
than just comparing two aggregate numbers.

Foreign keys tie everything back to `bug_reports` and `prompt_versions`, and
the schema treats `corrections` and `eval_results` as append-only, immutable
event logs, nothing is ever updated in place once written. That property is
also why the design is expected to port to a multi-writer production
database largely unchanged (see "Rearchitecting for a production
environment" below).

## What the loop actually demonstrated

Running the full loop produced a real, measured result on the 13 held-out
examples. Reviewing all 27 train reports yielded 13 corrections:

| | Severity accuracy | Component accuracy |
|---|---|---|
| v1, baseline (zero-shot) | 38.5% | 100.0% |
| v3, after 13 corrections | 84.6% | 100.0% |

Against the baseline that is 8 examples fixed and 2 regressed. The promotion
gate caught the regressions and refused to promote until they were reviewed
and explicitly overridden.

Two things about *how* corrections are turned into prompt text were measured
separately, by holding the correction set fixed and changing only the
rendering. One mattered a great deal and one did not at all.

**Rendering corrections faithfully was worth +15.4 points.** The original
renderer had two defects. It labelled an exemplar's entire before-state as
the model's "initial (incorrect) guess" even when the reviewer had changed
only one field, so a correction that fixed only the component still told the
model its correct severity had been wrong. And it printed the stored
rationale under "Why the human corrected it" when that text was, in
practice, the *model's own* rationale for its original answer: `review`
pre-filled the rationale prompt with the model's text, and a reviewer who
accepted the default left the model's justification for the overruled answer
attached to the corrected label. Exemplars therefore argued against their own
corrections, for instance a report corrected to `Low` carrying the reasoning
"creating a security vulnerability where sensitive credentials could be
observed". Showing only genuinely changed fields, marking unchanged fields as
confirmed-correct, and omitting the rationale unless the reviewer actually
wrote one was worth 46.2% to 61.5% on an 8-correction set, with no new
corrections and no regressions.

**Capturing the reviewer's written reasoning was worth nothing measurable.**
Since a pre-filled rationale is accepted verbatim, `review` now asks "why is
that the right call?" with an empty default, and only when a label actually
changed. Re-running the loop that way produced 13 corrections each carrying a
genuine written explanation of the principle behind it. Evaluated against the
identical 13 corrections with those explanations stripped out, the result was
**84.6% either way**: one example fixed, one regressed, no net movement. The
labels carry the signal; the prose does not add to it at this scale. The
prompt is still worth asking, because the explanations are what a human needs
in order to audit a correction later, and because the alternative that was
actually harmful was storing the model's own contradictory text. But it
should not be described as an accuracy improvement, because it measurably
isn't one.

One of those two regressions turned out to be more interesting than a simple
mistake. Report #3 ("password reset email leaks the password in plaintext,
described
in a deliberately mild tone") was correctly classified **Critical** by the
zero-shot baseline, with zero exemplars in play. After the corrections were
added, it flipped to **High**. Digging into what was actually retrieved for
it: TF-IDF pulled in two corrections from an unrelated theme (a password
field briefly showing unmasked characters on a login form, correctly
downgraded from High to Low) purely because both reports share the words
"password" and "plain text." The retrieval matched on surface vocabulary,
not on the causal category that actually determines severity (data/
credential exposure vs. a cosmetic display quirk), and dragged an
already-correct answer off course. I tried adding a targeted training
example to close what I first assumed was a coverage gap, and it didn't
help, because the model didn't need the extra exemplar to get this case
right in the first place; the actual mechanism was retrieval noise, not
missing signal. I reverted that change rather than keep tuning the dataset
against this one eval example, since that would be overfitting the eval set
rather than fixing the system.

The exemplar-rendering fix described above is what confirmed this diagnosis.
The self-contradictory rationales were an obvious suspect for report #3, and
fixing them helped substantially elsewhere, but #3 regressed afterwards
exactly as it had before, and it regresses again in the 13-correction run.
The two failures are independent: bad exemplar rendering was costing accuracy
across the board, while report #3 is specifically a retrieval-quality problem
that only better similarity matching, real embeddings rather than TF-IDF,
will fix. It has survived every change made to the prompt so far, which is
itself the evidence that the cause is retrieval rather than rendering.

I'm keeping this as the centerpiece example in this writeup rather than
finding an eval set where it doesn't happen, because it's exactly the
failure mode the "trustworthy" requirement is asking a system to catch: an
improvement in one place quietly costing correctness in another, for reasons
that aren't obvious from the aggregate accuracy number alone. The gate did
its job: it blocked the promotion and forced a human decision instead of
auto-promoting on net accuracy. Promoting anyway with `--force` is defensible
when 8 fixes stand against 2 regressions, but it should be a stated decision
rather than a silent one.

## Design Decisions

### 1. Retrieval-augmented prompting, over fine-tuning

**Retrieval-augmented few-shot prompting (recommended)**
- Pros: fast to iterate (no training job); reversible (undoing a change is
  just retrieving different exemplars, not discarding a checkpoint);
  inspectable (`exemplar_correction_ids` records exactly what influenced any
  given output); no training compute cost.
- Cons: bounded by context size and retrieval quality; only helps future
  reports that are textually similar to a corrected one; "textually similar"
  via TF-IDF is a blunt instrument that can actively mislead, not just fail
  to help (see the report #3 regression above).

**Fine-tuning**
- Pros: can generalize past surface-level textual similarity to a real
  decision boundary; no per-call retrieval or exemplar-injection cost at
  inference time; scales better once correction volume is very large.
- Cons: slow iteration loop (a training run, not a database write); real
  compute and data-pipeline cost; much harder to audit (no equivalent to
  `exemplar_correction_ids` for "why did the model say this"); effectively
  irreversible without keeping every checkpoint around.

Verdict: retrieval-augmented prompting fits a one-day build and a small
correction volume. Fine-tuning would earn its cost once correction volume is
large and generalizing past textual similarity becomes the actual
bottleneck; real embeddings (already listed under "what I'd do with more
time") is the nearer-term fix for the specific failure mode observed here.

### 2. Synthetic data, over a public dataset

**Synthetic data (recommended)**
- Pros: deliberately paired train/eval examples on the same confusion theme,
  guaranteeing the retrieval step has something relevant to find; full
  control over gold-label consistency; the demo's outcome depends on the
  mechanism, not on sampling luck.
- Cons: openly a constructed demonstration dataset, not found data; doesn't
  by itself prove the mechanism holds up against messier real-world text.

**Public dataset (real Bugzilla/GitHub issues)**
- Pros: real text, real distribution of how people actually write bug
  reports; more externally credible as evidence the system works on data it
  didn't get to shape.
- Cons: severity/priority labels in real trackers are self-assigned and no
  more trustworthy as "gold" than data written for this exercise; no
  guarantee train and eval examples share enough content for retrieval to
  find anything relevant, so the demo could fail for sampling-luck reasons
  rather than design reasons; still requires manual relabeling onto this
  system's taxonomy, which reintroduces the same subjectivity it was meant
  to avoid.

Verdict: synthetic data was the right call for proving the mechanism within
a day. Retrying with public data is listed under "what I'd do with more
time," now with a working harness to validate it against.

### 3. Any-regression gate, over an accuracy-threshold gate

**Any-regression gate (recommended)**
- Pros: catches an individual regression hidden inside a good-looking
  aggregate number, which is exactly what happened in this run: 84.6% against
  a 38.5% baseline would have auto-passed an accuracy-threshold gate while
  hiding two individual regressions; matches the fact that the underlying LLM
  serves a customer-facing product, so no individual regression should ship
  unnoticed.
- Cons: stricter and slower to promote; a single borderline example can
  block a promotion that's an obvious net win, forcing a manual `--force`
  even when the right call is clear.

**Accuracy-threshold gate**
- Pros: simpler to reason about; easier to automate into a CI-style pipeline
  (this is what Braintrust's approach does); doesn't block on noise from one
  borderline example.
- Cons: would have silently let the report #3 regression ship in this exact
  run, precisely the failure mode the "trustworthy" requirement is meant to
  catch.

Verdict: any-regression is the right default for something that reaches
customers. A CI-style accuracy floor (already listed under "what I'd do with
more time") is worth adding as a second, complementary check, not a
replacement.

### 4. Relational data model, over document/NoSQL or graph

The real question isn't which engine (SQLite vs. Postgres) but which data
model fits the actual access patterns.

**Relational (recommended)**
- Pros: the dominant queries (`get_exemplars`, `diff_runs`) are joins and
  correctness-bucket aggregations, exactly what SQL optimizes for; FK
  constraints enforce real invariants (a correction must reference exactly
  one output, which references exactly one report) instead of trusting
  application code to maintain them.
- Cons: schema changes (e.g., adding a field to `bug_reports`) require a
  migration; doesn't flex well if different report sources need
  different/inconsistent fields.

**Document/NoSQL**
- Pros: schema-per-record flexibility (real bug trackers have wildly
  inconsistent custom fields across teams); can be a natural fit if the
  dominant access pattern is single-key lookups at very high write
  throughput.
- Cons: no native joins: `diff_runs` and `get_exemplars` would need
  denormalized data (duplicating report text into every output/correction
  document, risking drift) or application-side joins, which is strictly
  more code than letting the database do it for the query patterns this
  system actually has. Rejected, not just deprioritized.

**Graph**
- Pros: `prompt_versions.parent_id` is already a lineage chain; if the
  system grew to support branching prompt experiments (two engineers
  improving from the same base version in parallel, later compared or
  merged), lineage traversal becomes a natural graph query and an awkward
  recursive-SQL one. There's a second latent graph in the retrieval
  mechanism too: "which corrections influenced which outputs" is
  implicitly a similarity graph (nodes = reports/corrections, edges =
  retrieval matches above threshold) that today is only reconstructable by
  re-running TF-IDF, not stored or queryable.
- Cons: pure overkill at current scale: one linear v1→v2 chain doesn't
  need graph traversal, and running a graph database is a real operational
  cost with no payoff until branching or influence-graph queries are
  actual product requirements, not hypothetical ones.

Verdict: relational is correct for this system's actual query patterns
now; graph is a legitimate, specific future-evolution path (branching
lineage or an explicit correction-influence graph) rather than a generic
aside, and NoSQL doesn't fit this workload regardless of scale.

### 5. Similarity-prefilter-then-LLM, over alternative retrieval architectures

The retrieval step is a two-stage pipeline: a cheap, non-LLM similarity
search (TF-IDF today, embeddings as the documented upgrade) narrows the full
correction history down to a handful of candidates, and only those
candidates are ever shown to the LLM. That's one point in a wider design
space for how the model decides what context to use, worth naming
explicitly rather than treating as the only option.

**Similarity prefilter, then LLM (recommended)**
- Pros: scales independently of correction volume, since the prefilter cost
  stays roughly constant per call given a proper index, rather than growing
  with correction history; keeps the LLM's context focused on a small number
  of relevant examples instead of diluted with irrelevant ones; every output
  is auditable back to the exact exemplars that influenced it
  (`exemplar_correction_ids`).
- Cons: the LLM only ever sees what the prefilter decided was relevant; if
  the prefilter is wrong, the LLM has no way to notice or recover, which is
  exactly what happened with the report #3 regression.

**No retrieval: include the full correction history in every prompt**
- Pros: simplest possible design, no separate retrieval step to build, tune,
  or get wrong; the model sees everything and can judge relevance itself
  rather than trusting an upstream filter.
- Cons: doesn't scale, this only works while the entire correction history
  fits comfortably in a context window; token cost and latency grow with
  every new correction; past a fairly small volume, most of what's included
  is irrelevant noise, and language models are known to perform worse when
  relevant information is buried among a lot of irrelevant context rather
  than presented cleanly.

**LLM-as-retriever: use the model itself to select or re-rank candidates**
- Pros: potentially better judgment of relevance than a similarity score,
  since it can reason about why something is relevant rather than just
  measuring word or vector overlap; can catch cases where two things are
  relevant to each other for a reason that has nothing to do with textual
  similarity.
- Cons: still requires some candidate list to choose from in the first
  place, so it doesn't remove the need for a cheap prefilter at real scale,
  it can only sit on top of one as an extra re-ranking step; adds a second
  LLM call's worth of cost and latency to every single triage call; harder
  to audit than a similarity score, "the model decided this was relevant" is
  a less inspectable answer than "this scored 0.31 on cosine similarity."

**Rule/lesson distillation: no runtime retrieval, compiled rules baked into
the prompt**
- Pros: no per-call retrieval cost or latency at all, the "lesson" is just
  static prompt text; generalizes past textual similarity in a way retrieval
  structurally cannot (a written rule like "Critical requires no workaround"
  applies regardless of whether a report's wording resembles any past
  example); easier to review as a diff to prompt text than as a changing set
  of retrieved exemplars.
- Cons: requires its own distillation step (an LLM summarizing patterns
  across corrections), which carries its own risk of overgeneralizing from a
  small number of examples; compiled rules can go stale or start to conflict
  with each other as more get added, with no natural mechanism for retiring
  one; loses the fine-grained audit trail retrieval gives for free, there's
  no equivalent of `exemplar_correction_ids` pointing back to exactly which
  corrections produced a given rule.

Verdict: similarity-prefilter-then-LLM is the right default, it's the only
option here that scales independently of both correction volume and context
window size while staying auditable. Rule distillation is the most
promising complement, not a replacement (already listed under "what I'd do
with more time"): retrieval handles textually-similar cases cheaply,
distillation handles the generalization retrieval structurally can't.
Fine-tuning is a more radical alternative to this entire retrieval-plus-
prompting approach and is covered separately in Design Decision 1.

One caution about the distillation option, from the measurement described in
"What the loop actually demonstrated": adding the reviewer's written reasoning
to each exemplar moved accuracy by exactly zero, while fixing which *labels*
an exemplar claimed were wrong was worth 15 points. Whatever the model is
picking up from an exemplar here, it is coming from the corrected labels
rather than from prose about them. That is weak evidence against expecting a
lot from distilled natural-language rules on this task, and a reason to
measure a distillation step against a held-out set before believing it,
rather than assuming that more explanation in the prompt means better
judgment.

## Assumptions and limitations

- The system is built for one specific task, bug-triage classification into
  `severity`/`component`/`rationale`, not a general-purpose tool. The
  severity levels, component list, and rubric text are hardcoded (see "Task
  extensibility" below for exactly where), so using this for a different
  structured-output task today would mean editing code in several places,
  not changing a configuration value.
- Rationale text is captured and stored as part of every correction (so it
  still feeds the exemplar bank), but is not scored in the eval harness:
  there's no reliable automatic gold-match for free text at this scope. Only
  severity and component are scored.
- Reviewer identity is a single email string (`REVIEWER_EMAIL` in `.env`, or
  `--reviewer` per invocation), not an auth system: nothing verifies it's
  actually the person typing. Fine for a one-day exercise, one reviewer at a
  time; would need real authentication and conflict handling for concurrent
  multi-reviewer use.
- TF-IDF retrieval, not real embeddings, chosen to avoid a second API
  key/dependency (Anthropic has no native embeddings endpoint; their
  recommended partner is Voyage AI) for a 40-report dataset where TF-IDF is
  "good enough" to demonstrate the mechanism, but it's also the direct cause
  of the regression documented above.
- `triage promote --force` is a manual override with no additional
  guardrail beyond having seen the diff; it doesn't require a written
  justification to be recorded anywhere. For a real system I'd want the
  override reason captured alongside the promotion, not just typed in a
  chat/PR description somewhere.
- 13-example eval set gives ~7.7-percentage-point resolution per example;
  enough to see a clear signal here, but a production system would want a
  larger held-out set before trusting small deltas.

## What I'd do with more time

- **Real embeddings for retrieval** (Voyage AI, or any embedding model) in
  place of TF-IDF, specifically to fix the surface-vocabulary-collision
  failure mode documented above: semantic similarity should separate
  "credential exposure" from "cosmetic masking delay" even though both
  mention "password."
- **Rule distillation as a second improvement mechanism**, alongside
  exemplar retrieval: periodically have an LLM look at clusters of
  corrections and propose an explicit, human-reviewed addition to the
  system prompt (e.g., "Critical requires no workaround; a feature being
  broken for many users with a workaround is High, not Critical"). This
  generalizes beyond textually-similar future reports in a way exemplar
  retrieval structurally cannot; it was the main mechanism candidate I
  scoped out for time, in favor of the more purely-mechanical exemplar
  approach.
- **Structure the prompt into named sections** (a static rubric vs. a
  growing "lessons learned" block) instead of one flat `system_prompt`
  string, so promotion diffs can show exactly what a new version's
  instructions gained, not just which exemplars it can now retrieve.
- **Retry the public-dataset path** (real GitHub issues or Bugzilla data)
  now that the loop mechanics are validated on synthetic data (see Design
  Decision 2), with a known-working harness to validate it against.
- A **CI-style automated accuracy floor** in addition to the per-example
  regression gate (an idea pulled from Braintrust's approach: block if
  accuracy drops below a bar), as a second, independent check alongside the
  per-example diff.

## Rearchitecting for a production environment

Everything above is a prototype optimized for building and validating the
loop mechanism itself in a day: SQLite, a single CLI process, TF-IDF
computed fresh on every call, a human manually typing eval commands. None of
that survives contact with real scale. Concretely, by layer:

- **Storage.** SQLite → Postgres. The current schema (six tables, foreign
  keys, an append-only `corrections` log) ports over largely unchanged; the
  design already treats corrections and eval results as immutable
  event-style records rather than mutated state, which is the part that
  actually matters for a multi-writer system. What changes is concurrency
  control (SQLite's single-writer lock is fine for one reviewer, not for a
  team) and adding proper migrations instead of one `schema.sql` run once.

- **Retrieval.** TF-IDF refit from scratch on every single triage call is
  O(correction-history size) per call, and since `run_eval` calls it once
  per eval example, a full eval run's retrieval cost is actually O(eval-set
  size × correction-history size), both knobs multiply together. It doesn't
  survive past a few thousand corrections, and as documented above, it's
  also the direct cause of the one regression this run produced. Production
  version: real embeddings (Voyage, since Anthropic has no native embeddings
  endpoint) written once per correction into a vector index (pgvector at
  moderate scale, a dedicated vector store beyond that) with approximate
  nearest-neighbor lookup, so retrieval cost is ~constant per call instead
  of growing with correction history. That embedding computation should be
  a **background job, not inline on the write path**: when a reviewer
  submits a correction, write it immediately and return, an embedding isn't
  needed until some future triage call retrieves it, not the instant it's
  created. A background worker picks up corrections without an embedding
  yet, computes them (ideally batched, since most embedding APIs are
  cheaper per item in batch than one at a time), and upserts them into the
  vector index. That keeps the reviewer-facing correction-submission path
  fast and independent of an external API's latency.

- **Reviewer model.** The `reviewer` column holds an email (`REVIEWER_EMAIL`
  or `--reviewer`) with no authentication behind it today, which is fine for
  one person running a CLI locally. Production needs that email backed by
  real auth, a proper review queue with assignment/
  locking (so two reviewers can't silently overwrite each other's correction
  on the same report), and the review surface itself probably becomes a
  panel inside whatever tool support engineers already triage tickets in,
  not a standalone CLI; the CLI's `review` command is a stand-in for "the
  review UX," not the final form of it.

- **Correction quality at volume.** With a single reviewer, every correction
  is trustworthy by construction: it's one person applying one rubric
  consistently. That assumption breaks the moment there are many reviewers
  submitting corrections concurrently (imagine an engineering org where
  thousands of people each correct triage output daily): reviewers disagree,
  vary in care and consistency, and a bad or inconsistent correction becomes
  a candidate exemplar with exactly the same weight as a good one. Nothing
  in the current design distinguishes a correction three reviewers
  independently agreed on from one person's one-off judgment call. At real
  volume this needs its own mechanism upstream of retrieval, for example
  requiring agreement across multiple reviewers before a correction becomes
  eligible as an exemplar, or weighting exemplars by a reviewer's track
  record, so that only trusted corrections ever reach the retrieval step
  this system already has.

- **Where inference actually happens.** Right now the CLI *is* the triage
  caller. In production, the customer-facing product's own backend is the
  thing calling the model on real traffic; this tool's job shifts to being
  a **prompt version store and correction-capture service** that the product
  backend reads from (give me the active prompt version for this task) and
  writes to (here's a human correction), rather than the thing doing
  inference itself. That's a real architectural split: a prompt-serving API,
  a correction-ingestion
  API, and this review/eval/promote tooling all become separate concerns
  sharing one data layer, instead of one CLI process doing everything.

- **Task extensibility.** Everything here is currently hardcoded to bug
  triage in a specific, countable way: `severity`/`component` appear as
  named columns in 14 places across `schema.sql` (`bug_reports`,
  `model_outputs`, `corrections`, `eval_results`), plus `TRIAGE_TOOL` and
  `BASE_RUBRIC` in `llm_client.py`, plus `SEVERITY_LEVELS`/`COMPONENTS` in
  `config.py`. None of that is accidental complexity, but none of it
  generalizes to a different task (say, support-ticket routing, or contract
  clause classification) without editing code in four places at once. The
  loop mechanism itself doesn't have this problem: `prompt_versions`,
  retrieval (`get_exemplars` just operates on arbitrary input text), and the
  regression-gate/diff logic are already task-agnostic in structure. Making
  the system extensible means introducing a **task definition** (a name, a
  structured-output schema, the rubric text, and which fields count toward
  eval scoring) that parameterizes what's currently hardcoded, and switching
  `bug_reports`/`model_outputs`/`corrections`/`eval_results` from named
  columns to a `payload` blob validated against that task's schema, with
  `diff_runs`'s correctness check iterating over the task's declared scored
  fields instead of hardcoding `severity_correct`/`component_correct`.

  Once each task has its own versioned output schema, and especially once
  those schemas are read by more than one service (this tool, the product
  backend, maybe several different product surfaces each running their own
  task), a schema definition language with real field-level compatibility
  guarantees, protobuf or an equivalently strict JSON Schema plus a
  compatibility linter, is what lets one task's schema evolve (a new field
  added, a new severity level introduced) without breaking already-stored
  historical corrections and eval results for that task, or breaking a
  different task's schema entirely. That compatibility guarantee is the main
  justification, and it matters more as the number of distinct tasks grows.
  Protobuf's compact binary encoding is a nice added benefit on top, smaller
  payloads and faster serialization, though not the deciding factor here:
  payloads are small (a prompt string plus a few exemplars, kilobytes at
  most) and the call pattern is naturally low-QPS (a client caches the
  active prompt version and polls for changes rather than re-fetching per
  triage call, and correction-ingestion volume is bounded by human review
  throughput, not machine throughput), so the encoding-efficiency win is a
  bonus rather than a necessity at this system's scale.

- **Rollout, not just promotion.** `triage promote` today is a binary
  switch: a version is active or it isn't, decided from an offline eval
  against 13 fixed examples. At real scale, a promoted version should first
  go out as a **canary** to a small percentage of live traffic, with online
  metrics (does live accuracy match the offline eval? do downstream signals
  like ticket reopens or escalations move?) monitored before full rollout,
  because an offline eval set, however carefully built, can go stale or miss
  a real-world case the 13-example set never covered. This could be staged
  through regions and environments (dev, then a low-traffic region, then
  globally) rather than one global flip, and combined with a gradual traffic
  switch, a rolling update that shifts an increasing percentage of requests
  from the active version to the candidate rather than cutting over all at
  once, so a bad promotion degrades a shrinking slice of traffic instead of
  all of it, and rollback (below) only has to reverse whatever slice has
  already moved. The existing `parent_id` lineage on `prompt_versions`
  already gives cheap rollback (reactivate the parent) for free; that part
  of the design already generalizes.

- **Eval at scale, and a continuous versioning cadence.** A fixed 13-example
  eval set gives ~8-point resolution per example, which is fine for a
  one-day demo and not fine for real decisions. Production needs a
  continuously-growing, stratified eval set (sampled across
  severity/component/theme so no category is under-represented), automated
  eval runs on a schedule rather than a human typing `triage eval`, and
  probably the CI-style accuracy-floor gate noted above running as an actual
  pipeline step rather than a manual command. `triage improve` has the same
  problem in the other direction: today it's a human manually bundling
  "everything since last promotion" into one batch. At real correction
  volume that has to become a scheduled, continuous process too, with
  candidate versions created and evaluated on a cadence, and human review
  reserved for promotions the automated gate can't clear on its own rather
  than every single one.

- **Observability.** Every triage call already logs which exemplars it used
  (`exemplar_correction_ids`), which is the right instinct but currently
  only queryable by hand in SQLite. Production wants this as structured logs
  feeding a real dashboard: accuracy trend per prompt version over time,
  correction volume and reviewer throughput, cost and latency per triage
  call, and alerting if a live prompt version's behavior drifts from what
  its last eval run predicted.

The throughline across all of these: nothing about the *data model* or the
*loop's logic* (corrections → exemplars → gated eval → gated promotion)
needs to change to get to production; what changes is everything about how
each step is served, scaled, and made concurrent-safe. That's a reasonable
sign the core design is sound; the prototype cut corners on infrastructure
around it, not on the mechanism itself.
