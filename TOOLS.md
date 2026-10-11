# TauCetiWorker Tool Capability Specification

## Goal

Add a small, explicit mechanism for making trusted worker tools available to Tau Ceti agents.

The initial use case is a shared local Loogle service. The design should also support later capabilities such as Lean LSP/MCP integration, preflight commands, dependency analysis, and other experimental tools.

This is intentionally separate from `--source`.

* `--source` provides untrusted supplementary mathematical material.
* `--tool` enables trusted executable capabilities managed by TauCetiWorker.

## CLI

Add a repeatable option:

```sh
tauceti work --tool loogle
tauceti work --tool loogle --tool lean-lsp
```

No tools are enabled by default.

Managed workers should support the equivalent configuration:

```toml
[[workers]]
id = "mathos"
tools = ["loogle"]
```

The selected tool set must propagate unchanged through `tauceti loop` child invocations.

Unknown tool names are fatal configuration errors.

## Tool definition

TauCetiWorker should maintain an internal registry of supported tools. A tool definition may specify:

```text
name
eligible worker phases
scripts to expose
environment variables
Bubble mounts
model-facing prompt fragment
health/doctor check
optional setup hooks
```

The initial implementation does not need a general third-party plugin API. Tool implementations may live directly in TauCetiWorker.

Tools are trusted code distributed with, or explicitly supported by, TauCetiWorker.

## Agent exposure

Enabling a tool must do two things:

1. Make the capability technically accessible.
2. Tell the agent that the capability exists and how it should be used.

TauCetiWorker should therefore support a common trusted tool-prompt prelude.

For example, enabling `loogle` could append:

```text
A shared Tau Ceti Loogle service is available through:

  __BIN__/tauceti-loogle '<query>'

Use it to search Mathlib and canonical Tau Ceti main for declarations.
It does not contain branch-local changes. Confirm results in the current
checkout before relying on them.
```

Prompt fragments should be short and descriptive rather than prescribing a large workflow.

## Host and Bubble behavior

Tools must behave consistently between ordinary and Bubble rounds.

For command-line helpers:

* Host rounds may use the packaged TauCetiWorker script directly.
* Bubble rounds should stage the same script in `/opt/round`.
* Agent prompts should use TauCetiWorker's existing `__BIN__` expansion rather than relying solely on `PATH`.

Tools may additionally request explicit Bubble mounts.

Example for Loogle:

```text
host:
  tauceti-loogle → 127.0.0.1 Loogle service

Bubble:
  /opt/round/tauceti-loogle
      ↓
  mounted /run/tauceti-loogle/loogle.sock
      ↓
  host Loogle service
```

A tool must not receive broader filesystem or network access than necessary for that capability.

## Environment metadata

TauCetiWorker should expose basic round identity to trusted tool wrappers:

```text
TAUCETI_WORKER_ID
TAUCETI_PHASE
TAUCETI_AGENT
TAUCETI_MODEL
TAUCETI_ROUND_ID
```

Values unavailable in a given execution mode may be omitted.

This allows tool requests and telemetry to be attributed to a worker round without hard-coding worker-specific behavior into individual tools.

## Availability and failure

Optional tools should normally fail open.

If `--tool loogle` is enabled but the local Loogle service is unavailable:

* the wrapper should return a clear nonzero result;
* the worker round should continue;
* the agent should be able to fall back to ordinary Lean and source-search tools.

Configuration errors, such as an unknown tool name or an invalid tool definition, should fail before dispatch.

Tools that are essential to a future worker mode may choose stricter semantics explicitly.

## `tauceti doctor`

`tauceti doctor` should report enabled or discoverable tools separately from core worker requirements.

Example:

```text
[ok      ] loogle       shared Tau Ceti declaration search
[missing ] lean-lsp     optional agent-facing Lean LSP tools
```

A missing optional tool should not make the general doctor command fail unless that tool was explicitly requested for the current worker configuration.

## Telemetry

Every round should record the selected tool set:

```text
tools = ["loogle", "lean-lsp"]
```

Tool-specific telemetry may additionally record:

```text
tool version
queries/calls
latency
success/failure
result count
```

Per-round or per-worker identifiers should go into structured logs rather than high-cardinality Prometheus labels.

This is important for controlled A/B testing of agent capabilities.

## Initial `loogle` tool

The first implementation should define:

```text
name: loogle
phases: roadmap, fix, fix-ci, rebase, bump
script: scripts/tauceti-loogle
Bubble mount:
  $TAUCETI_LOOGLE_SOCKET_DIR:/run/tauceti-loogle:ro
```

The wrapper should query a separately managed persistent Loogle service.

TauCetiWorker should not build or own the Loogle index as part of an ordinary worker round.

Suggested environment:

```text
TAUCETI_LOOGLE_SOCKET_DIR
TAUCETI_LOOGLE_URL
```

The wrapper should propagate worker metadata in request headers where available.

## Non-goals

The first version should not:

* turn `--source` into an executable-tool mechanism;
* execute arbitrary tool repositories supplied by URL;
* provide a general package manager;
* inherit arbitrary host Codex or Claude configuration;
* modify the Tau Ceti source tree to install tools;
* require every worker to use enabled tools;
* embed large agent workflow instructions in tool definitions.

The feature should remain a small capability layer that gives experiments precise control over which trusted tools an agent can access.
