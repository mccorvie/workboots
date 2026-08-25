# Work Boots Telemetry

**Status:** Initial design  
**Last updated:** 2026-08-23

## 1. Goal

Work Boots is the observability and experimental-control layer around
[TauCetiWorker](https://github.com/kim-em/TauCetiWorker). It should make a Tau
Ceti worker fleet measurable, reproducible, and controllable without replacing
the Worker machinery that already exists.

The system should:

- Record what each worker was configured to do and what it actually did.
- Reconcile local execution with GitHub outcomes and, for reviews, with
  [TauCetiData](https://github.com/TauCetiProject/TauCetiData).
- Track provider quota, GitHub API budget, failures, backoff, and human
  intervention.
- Support finite randomized experiments such as ten Codex `medium` attempts
  versus ten Codex `xhigh` attempts.
- Record tool use, beginning with the proposed Tau Ceti-aware Loogle service.
- Backfill the first exploratory Mathmos/Pygar run.

The central design decision is:

> **TauCetiWorker continues to manage the ordinary persistent fleet. Work Boots
> observes that fleet and owns only finite experiments and telemetry.**

Work Boots should not reimplement TauCetiWorker's queue survey, phase cascade,
claims, safe-push machinery, quota calculations, or persistent-worker manager.
It should not duplicate TauCetiReview's detailed review schema, and TauCetiData
should not become the orchestration database.

## 2. Existing systems and responsibility split

| System | Existing responsibility | Work Boots relationship |
| --- | --- | --- |
| **TauCetiWorker** | Surveys the queue, chooses a phase, coordinates claims, selects providers, applies pacing, invokes agents, and manages persistent workers. | Observe it in normal operation; invoke one-shot rounds in finite experiments. |
| **TauCetiReview** | Runs review rubrics and models, posts scoreboards/findings, and writes local review records. | Link Worker attempts to its review-round and rubric-run IDs. Preserve its store and outbox. |
| **TauCetiData** | Public durable archive and evaluation dataset for Tau Ceti AI **reviews**. | Treat it as the authoritative public review dataset. It does not cover other Worker phases. |
| **GitHub** | Canonical public record of PRs, commits, checks, review threads, contests, labels, closure, and merge. | Reconcile local attempts to public artifacts and eventual outcomes. |
| **Work Boots** | Worker/experiment metadata, ingestion, provenance, finite stopping rules, reconciliation, and analysis. | Fill the gaps between the existing systems rather than replacing them. |

TauCetiData is review-specific. For `roadmap`, `fix`, `fix-ci`, `rebase`,
`bump`, and `progress`, the principal external record is GitHub, supplemented by
Worker and agent logs. For `review`, Work Boots should add orchestration context
and link it to TauCetiReview/TauCetiData.

## 3. Architecture and operating modes

```text
                       NORMAL CONTINUOUS FLEET

                            workers.toml
                                 │
                                 ▼
                    TauCetiWorker manager
                    (eventually systemd --user)
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
          review worker                    maintenance worker
          tauceti work --loop              tauceti work --loop
                 │                               │
                 └──────────────► GitHub ◄───────┘
                                 │
                                 ▼
                      TauCetiReview store/outbox


                         WORK BOOTS COLLECTOR

   worker state + logs ──────────┐
   review logs/store/outbox ─────┤
   provider/GitHub budgets ──────┼──► append-only events.jsonl
   GitHub reconciliation ────────┤              │
   TauCetiData ──────────────────┘              ▼
                                      dashboards and analysis
 

                         FINITE EXPERIMENTS

                       experiment manifest
                                 │
                                 ▼
                      Work Boots experiment runner
                                 │
                  randomized one-shot `tauceti work`
                                 │
                       stop after N qualifying
                         attempts in each arm
```

### Continuous fleet

TauCetiWorker reads `workers.toml` and supervises persistent
`tauceti work --loop` processes. Work Boots runs as a passive sidecar: it
snapshots configuration, tails logs, ingests review records, samples resource
state, and reconciles outcomes. There should be no `workboots loop` in the
first implementation.

### Finite experiments

Work Boots invokes one-shot `tauceti work` commands. Each invocation performs
one survey/child round and exits. Work Boots chooses the randomized arm,
brackets the round with telemetry, and continues until the preregistered number
of qualifying attempts has been reached.

A no-work survey, claim collision, provider block, or GitHub failure remains in
the dataset but ordinarily does not count as a completed model attempt.

### Paired replay and shadow review

For lower-variance comparisons, both arms should receive the same frozen task:
same PR or historical target, head SHA, TauCeti/Mathlib revisions, prompt,
rubric, tool profile, and cap. Each arm runs in a fresh no-push worktree, and
only one selected result may be published.

Review comparisons should use TauCetiReview's shadow/pairing machinery where
possible, since it already records rubric-level runs on the same
`(PR, head SHA, rubric)` unit.

### Deployment

The initial backfill can run under the current login. After existing evidence
has been preserved, the intended unattended deployment is a dedicated Unix
user with a dedicated GitHub agent identity:

```text
tauceti user
├── TauCetiWorker manager       (systemd --user)
├── persistent Worker loops     (managed by TauCetiWorker)
├── Work Boots collector        (separate systemd --user service)
├── Worker state/logs/checkouts
└── Work Boots ledger/database
```

Running the collector under the same Unix identity simplifies path access and
attribution. Operator-facing reports can be read from the normal login, while
maintenance can use `sudo -iu tauceti`.

## 4. Evidence sources

Work Boots should preserve provenance and fidelity for every observation.
These sources are complementary rather than interchangeable.

| Source | Information | Limitation |
| --- | --- | --- |
| `workers.toml` | Desired scope, model/effort, pace, sandbox, environment, enablement. | May be invalid or newer than the running generation. The manager can keep its last good generation. |
| `manager.log` | Configuration failures and manager decisions. | Durable but sparse. |
| Managed `<worker>.json` | Live instance, PID, spec hash, state, target, backoff, active log, last failure. | Overwritten in place; not a historical ledger. |
| `work-*.log` | Loop/session chronology, surveys, queue snapshots, selected phase/target, model settings, child-log paths, return codes, and backoff. | Durable and parseable, but textual and sometimes noisy. |
| `agent-*` logs/transcripts | Code-writing model execution, tools, builds, and often usage. | Provider/version-dependent format. |
| `review-*` logs | TauCetiReview execution, rubric runs, posting, exact run metadata, failures, and usage. | More authoritative than the parent Worker return code for determining whether review analysis completed. |
| TauCetiReview store/outbox | Structured review records awaiting or completing publication. | Must be preserved when direct TauCetiData publication is unavailable. |
| TauCetiData | Public review runs/rounds, verdicts, findings, usage, hashes, and scrubbed artifacts. | Review-only; intentionally lacks local worker configuration and identity. |
| GitHub | Public artifacts and eventual outcome. | Cannot reconstruct exact local model runs or configuration by itself. |
| Provider usage/status | Session/weekly consumption and capacity blocks. | May be exact, cached, estimated, or unavailable. |
| GitHub rate-limit endpoint | REST/GraphQL/search remaining budget and reset times. | Shared by all activity under the authenticated actor. |

The collector should discover actual paths from Worker status and logs rather
than hard-coding paths from one installation.

## 5. Canonical entities and identifiers

The data model must distinguish the Worker scheduling envelope from task
execution and from TauCetiReview terminology.

### `worker_definition`

One desired `workers.toml` specification: worker ID, phases, agent/provider
policy, author model/effort, review policy where observable, pace,
`ignore_quota`, sandbox, environment, and specification hash.

### `worker_session`

One persistent worker-process lifetime. Editing or restarting a worker begins a
new session even if the worker ID is unchanged. Record process IDs, effective
specification hash, start/stop times, work-log path, GitHub actor, host, and all
relevant software/prompt revisions.

### `cycle`

One TauCetiWorker survey/child round. A cycle may end without selecting work.
Record queue snapshot, provider and GitHub gates, selected phase, and outcome.

### `task_attempt`

One execution after a phase and target have been selected. Record repository,
PR/branch/head, roadmap area where applicable, requested and effective
provider/model/effort, sandbox, tool profile, start/finish, and independent
outcomes.

A stable `work_item_id` should connect repeated work on the same underlying job,
for example:

```text
review → fix → re-review → contest → review answer
```

Where possible, record `parent_attempt_id` so causal chains do not have to be
inferred only from timestamps.

### `agent_execution`

One actual provider/model invocation. A selected task may fail before an agent
starts, and a task may contain more than one provider attempt or rubric run.
Record exact model settings, usage/cost with source and confidence, status, and
log/transcript references.

### `review_round` and `review_run`

Use TauCetiReview's existing definitions and IDs:

- A **review round** is one review-engine invocation over a PR/head and may run
  several rubrics.
- A **review run** is one rubric × PR head × model execution and is the primary
  statistical unit in TauCetiData.

Work Boots stores linking metadata rather than duplicating the public schema.

### `experiment`

One preregistered comparison: hypothesis, mode, phase, filters, arms, exact
model/tool settings, randomization seed, qualifying-attempt definition, target
sample size, cap, and publication policy.

Use UUIDv7 or ULID for live identifiers. Backfilled events should use
deterministic IDs derived from source checksum, location, event type, and
normalized content so re-ingestion is idempotent.

## 6. Append-only event ledger

Raw observations should enter an append-only JSONL ledger before being
normalized. Derived tables may be rebuilt; raw events should not be silently
rewritten.

A representative event is:

```json
{
  "schema_version": 1,
  "event_id": "01K...",
  "event_type": "attempt_finished",
  "observed_at": "2026-08-23T20:10:21.442Z",
  "occurred_at": "2026-08-23T20:10:20.991Z",
  "worker_id": "agent-maint",
  "worker_session_id": "01K...",
  "cycle_id": "01K...",
  "attempt_id": "01K...",
  "experiment_id": null,
  "payload": {
    "phase": "fix",
    "repo": "TauCetiProject/TauCeti",
    "pr": 4166,
    "head_sha": "92709e425a45...",
    "agent_outcome": "completed",
    "semantic_outcome": "commits_created",
    "publication_outcome": "pushed"
  },
  "source": {
    "kind": "worker_log",
    "path": ".../work-20260822-024105.log",
    "line": 27,
    "parser_version": "workboots-0.1.0"
  },
  "fidelity": "exact"
}
```

Every reconstructed field should be marked:

- `exact`: directly present in a structured record or unambiguous log marker.
- `reconstructed`: deterministically derived from several exact observations.
- `inferred`: plausible but not uniquely established.
- `unknown`: unavailable.

Initial event types should cover configuration, session start/finish,
cycle start/finish, quota and GitHub-budget observations, survey completion,
task selection, attempt start/finish, agent start/finish, publication,
review-run linkage, GitHub artifacts, final outcomes, and tool queries.

The first collector can infer these from current files. After the schema is
validated, a small optional TauCetiWorker hook should emit exact core boundaries,
for example:

```text
TAUCETI_EVENT_LOG=/absolute/path/events.jsonl
```

Work Boots-specific analysis should remain outside TauCetiWorker.

## 7. Outcomes must be multidimensional

A Worker return code is not a sufficient outcome. The exploratory logs contain
reviews where model analysis completed and produced a verdict, but GitHub
posting failed; the parent Worker then reported failure. That is an
infrastructure/publication failure, not a model failure.

Store independent dimensions:

| Dimension | Example values |
| --- | --- |
| **Cycle** | `selected`, `no_eligible_work`, `claimed_elsewhere`, `provider_blocked`, `github_blocked`, `survey_failed`, `interrupted` |
| **Agent** | `not_started`, `completed`, `failed`, `timed_out`, `parse_failed`, `cancelled` |
| **Semantic** | `no_change`, `commits_created`, `ci_green`, `review_approve`, `review_request_changes`, `report_created`, `conflict_resolved` |
| **Publication** | `not_applicable`, `pushed`, `pr_opened`, `comment_posted`, `stored_in_outbox`, `publication_failed`, `stale_head_rejected` |
| **Final** | `merged`, `closed_unmerged`, `superseded`, `open`, `human_edits_required`, `unknown` |

A small top-level class such as `productive`, `no_progress`,
`provider_blocked`, `github_blocked`, `failed`, `timed_out`, or `interrupted`
may be derived under a versioned policy. Changing that policy must not alter the
raw evidence.

## 8. Collection and reconciliation

### Snapshot

`workboots snapshot` should create a sanitized, checksummed bundle containing:

- Current `workers.toml`, manager log, and worker-state JSON.
- `tauceti doctor`, `workers status --json`, `status --json`, and `usage --json`.
- Work, agent, and review logs.
- TauCetiReview stores, ledgers, and outboxes.
- Checkout refs and relevant reflogs.
- Worker, TauCeti, TauCetiReview, TauCetiData, Mathlib, prompt, rubric, and tool
  revisions where available.
- GitHub actor, non-secret Git identity, timezone, and host metadata.
- Manifest and SHA-256 checksums.

It must not copy access tokens, OAuth files, credential stores, or broad
environment dumps.

### Passive collection

`workboots collect` should watch/tail files without duplicating events, ingest
new review records, poll live Worker state modestly, and sample provider/GitHub
resource state on meaningful transitions. It should enqueue reconciliation
rather than perform an expensive global GitHub survey for every log line.

Use inotify where practical, with polling as a recovery path. Preserve source
path, source position, source checksum, and parser version for every event.

### Reconciliation

Link local attempts to external records using the strongest available key:

1. Exact TauCetiReview `run_id` or `round_id`.
2. GitHub comment/thread/check IDs recorded by TauCetiReview.
3. `(repo, PR, head SHA, rubric, provider/model, started_at)`.
4. `(repo, PR, head SHA, phase, local timestamp)`.
5. Heuristic timestamp/filename matching, marked `inferred`.

GitHub reconciliation should capture commits and force-pushes, checks and time
to green, review threads and contests, labels, merge eligibility, closure or
merge time, and whether later human commits materially changed the result.
Final outcomes should be revisited until terminal, with bounded follow-ups such
as one, seven, and thirty days where useful.

## 9. TauCetiReview and TauCetiData

TauCetiReview already owns detailed review telemetry. For each review attempt,
Work Boots should retain only the missing orchestration context:

- Worker/session/cycle/attempt IDs.
- Experiment and arm.
- Worker configuration and revisions.
- PR and exact head SHA.
- TauCetiReview round/run IDs.
- Local review-log/store paths.
- GitHub posting outcome.
- Outbox publication state.

TauCetiData should remain the public review archive and the source for
rubric-level verdicts/findings, provider attempts, token usage, estimated cost,
duration, hashes, scrubbed artifacts, and review evaluation. It should not be
expected to contain Worker IDs, local accounts, pace curves, sandbox settings,
or non-review tasks.

The supported publication path for external contributor outboxes must be
confirmed with the Tau Ceti maintainers. Until then:

- Preserve every outbox record.
- Track `local_only`, `sync_pending`, `published`, and `sync_failed`.
- Do not assume that a posted GitHub review was archived successfully.
- Do not delete records after an unsupported fork-based or manual copy.

## 10. Provider quota and GitHub budget

These are separate fleet resources and must be measured separately.

For provider quota, record the configured pace, `ignore_quota`, provider
windows and consumption, cache age, requested/effective model and effort,
fallback reason, pre-launch blocks, and concurrent active attempts.

TauCetiWorker pacing is a pre-launch gate, not an atomic fleet reservation. Two
workers can both observe enough headroom and launch. Work Boots should measure
per-round consumption and its upper tail before adding reservations.

The first exploratory run deliberately disabled quota pacing. Treat it as an
unpaced demand and observability run, not evidence that a pace curve is safe.

For GitHub, record authenticated actor plus REST/core, GraphQL, and search
remaining/used/limit/reset fields. Classify failures as primary REST, GraphQL,
search, secondary/abuse, network, or unknown. The first run showed that GitHub
failure can dominate logs and can turn completed review analysis into a failed
publication.

A dedicated GitHub agent account isolates fleet usage from interactive terminal
use and makes attribution cleaner. All workers under that identity still share
its buckets. A fleet-wide GitHub budget gate may be added later if independent
Worker preflights prove insufficient.

## 11. Finite experiments

A finite experiment should have an archived manifest. For example:

```toml
[experiment]
id = "codex-effort-fix-001"
mode = "live_queue"
phase = "fix"
qualifying_attempts_per_arm = 10
count_unit = "agent_started"
randomization = "blocked"
seed = 20260823
timeout_minutes = 90
publish = true

[[arms]]
name = "medium"
agent = "codex"
author_model = "gpt-5.6-sol"
author_effort = "medium"
tool_profile = "baseline"

[[arms]]
name = "xhigh"
agent = "codex"
author_model = "gpt-5.6-sol"
author_effort = "xhigh"
tool_profile = "baseline"
```

The runner randomizes arm assignment and invokes one-shot Worker rounds. An arm
counts only when its declared qualifying event occurs. No-work surveys, claims,
provider blocks, GitHub failures before model launch, and stale targets remain
telemetry but do not ordinarily satisfy the sample target.

Each experiment must pin or record Worker/TauCeti/Mathlib/prompt/rubric/tool
revisions, exact provider/model/effort, sandbox, caps, cache policy, GitHub
identity, quota state, and GitHub budget.

Do not compare raw averages across phases. A live-queue experiment measures
performance under natural task variation; paired replay is required for a
direct same-task comparison. Never run two publishing review arms on the same
PR/head; use shadow review or no-push replay.

## 12. Tool telemetry

Tools are part of the treatment. Every attempt should name a versioned
`tool_profile`, for example `baseline` or `loogle-local-v1`.

For the Tau Ceti-aware Loogle service, record attempt/worker IDs, query or
privacy-preserving hash, timestamp, latency, index/toolchain revision, hit
count, returned results, selected result where observable, warm/cold state, and
errors. The Loogle pilot should otherwise hold model, effort, task, prompt,
revisions, and cap constant.

## 13. Storage, commands, and dashboard

Use:

1. Immutable snapshots and copied evidence.
2. Append-only JSONL as the raw ledger.
3. SQLite in WAL mode as the normalized operational database.
4. Optional Parquet/DuckDB exports later.

Suggested layout:

```text
~/.local/share/workboots/
├── snapshots/<snapshot-id>/
├── events/YYYY/MM/DD/events.jsonl
├── db/workboots.sqlite
├── experiments/<experiment-id>/
├── reports/
└── state/ingestion-cursors.json
```

The collector should be the single logical writer. Appends must be locked;
SQLite writes must be transactional and idempotent.

Initial commands:

```text
workboots doctor
workboots snapshot
workboots backfill <snapshot-or-path>
workboots collect
workboots reconcile
workboots status
workboots report
workboots experiment run <manifest>
workboots experiment status <experiment-id>
```

The first dashboard/report should show current worker state and effective spec,
recent cycles/attempts, provider quota, GitHub budget, no-work/blocked/failure
rates, review-outbox backlog, fleet-created PRs, and experiment progress by arm.

A useful long-run fleet objective is:

> Review-clean merged roadmap value per total provider usage and wall time.

“Roadmap value” may initially require proxies such as merged PRs, accepted
progress reports, or a small human score. It should not silently become raw PR
count or lines changed.

Phase-specific metrics should include:

- `review`: judged correctness/precision, missed defects, contest outcome, cost,
  and latency. Finding count is yield, not quality.
- `fix`: findings cleared on next review, regressions, CI, number of fix cycles,
  and later human edits.
- `fix-ci`: first-attempt green rate, time to green, and failure class.
- `rebase`: green result without semantic loss or new review deficiencies.
- `roadmap`: review-clean merge, time to merge, review/fix rounds, and human
  edits.
- `bump`: build restored, migration correctness, regressions, and time to green.
- `progress`: report publication, factual accuracy, acceptance, and correction.
- operations: productive/no-op/blocked/timeout rates, queue wait, GitHub use,
  and human intervention.

Repeated attempts on a PR are correlated; analyses should cluster by PR or
`work_item_id`. Contests should be marked upheld, rejected, or unresolved.

## 14. Backfill of the first run

The Mathmos/Pygar run is the parser-validation dataset. Preserve and ingest:

- Both `work-*.log` files and worker-state JSON files.
- `manager.log` and all recoverable versions of `workers.toml`.
- Referenced agent/review logs.
- Both TauCetiReview stores and outboxes.
- Checkout refs/reflogs and installed revisions.
- Manually observed provider-consumption intervals.

It is not a clean experiment: pacing was disabled, both workers shared a GitHub
identity, GitHub rate limiting caused failures, scopes changed across sessions,
the configuration became invalid while the manager retained its last good
generation, and review records remained local.

It is nevertheless sufficient to validate reconstruction of worker sessions,
cycles, selected phases and targets, model settings, log links, independent
outcomes, backoff, infrastructure failures, outbox records, and eventual GitHub
outcomes. Exact retrospective per-round subscription consumption is not
available; manual interval observations should be stored with explicit
uncertainty.

## 15. Implementation order

1. Preserve the first run before upgrading or cleaning anything.
2. Define event schema v1, including provenance, fidelity, and independent
   outcome dimensions.
3. Implement `snapshot` and checksummed evidence bundles.
4. Implement backfill parsers into JSONL and SQLite.
5. Validate selected PRs, comments, commits, reviews, outboxes, and merges
   against GitHub and TauCetiData.
6. Implement passive live collection around one conservative persistent worker.
7. Add provider and GitHub-budget sampling and establish per-phase tails.
8. Prototype the optional structured TauCetiWorker event sink and compare it
   with parser reconstruction.
9. Implement finite manifests, randomized assignment, qualifying-attempt
   stopping rules, and no-push replay.
10. Resolve the supported TauCetiData contributor-publication path.
11. Add Loogle telemetry and run the matched pilot.
12. Increase concurrency only after resource tails and failure behavior are
    understood.

Candidate upstream changes, after validation, are:

- Optional structured session/cycle/attempt events.
- Explicit requested/effective model and effort in structured status.
- A durable completion callback for external quota reservations.
- Supported publication of contributor TauCetiReview outboxes.
- A shared GitHub-budget gate if multi-worker races remain material.

Telemetry v1 is complete when it can ingest without duplication, reconstruct
each session and child round, distinguish no-work from task attempts, record
effective configuration and revisions, link reviews to TauCetiReview/TauCetiData,
link other phases to GitHub outcomes, separate agent/publication/final outcomes,
run a finite randomized experiment to a qualifying-attempt stopping rule, and
backfill the first Mathmos/Pygar run with explicit fidelity labels.

## Evidence basis

This design was derived from the Work Boots Linux handoff dated 2026-08-23,
the first Mathmos and Pygar worker/state/manager logs, and the current
TauCetiWorker, TauCetiReview, and TauCetiData documentation and implementation.
The handoff was initially checked against TauCetiWorker commit
`24f1c74991403dfa8475e287fc5e7b7247ea8576`.

Every session and experiment must record software and prompt revisions because
these repositories are active and their behavior may change after this
document's status date.
