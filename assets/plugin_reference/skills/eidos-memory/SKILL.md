---
name: eidos-memory
description: "Git-native team operational memory (eidos/memory/mem-*.adoc). Load when corrected or after hitting a non-obvious build/test/CI/tooling gotcha, when reconciling stale/expired memories surfaced in eidos context or eidos drift, or when auditing or promoting team notes into formal DEC-*/INV-*/POL-* specs."
---
# Eidos Team Auto-Memory (`eidos-memory`)

`eidos/memory/` stores **shared, Git-tracked team operational memory** (`type: agent/memory`, one `mem-*.adoc` file per entry) across all coding agents. Personal developer preferences never belong here.

## 1. When to Save Automatically (and Disclose)

Run `eidos memory add` proactively—and explicitly report the created or updated `MEM-*` file path in your reply so the engineer can review or revert it in Git—whenever:
* The user corrects a repo-specific engineering assumption, test/build convention, toolchain quirk, or workflow mistake, **or** you self-discover a non-obvious build/test/CI/tooling trap (`--category feedback`, no TTL).
* You uncover a **time-bounded** operational constraint, release freeze, CI cap, or active migration caveat (`--category project --expires YYYY-MM-DD`).
* The user points to a shared operational dashboard, staging URL, or benchmark resource (`--category reference`).

`--summary` is optional (auto-derived from `--guidance` or `--title`). Passing `--file` or `--from-diff` automatically infers owning `CMP-*`/`CNT-*` entities (`--no-auto-entities` opts out) so notes attach automatically in `eidos context`:

```bash
eidos memory add --title "Use pnpm in web frontend" --category feedback \
  --guidance "Use pnpm exec vitest and pnpm build inside apps/web; npm fails on devEngines." \
  --why "package.json devEngines pins pnpm." \
  --file "apps/web/**" --json

# Anchor directly to uncommitted diff files (excluding eidos/memory/**)
eidos memory add --title "Keep integration DB fixtures real" --guidance "Pass tmp_path SQLite DBs." --from-diff --json
```

When a note already exists (or overlap warnings appear), edit it in place rather than duplicating:

```bash
eidos memory update MEM-USE-PNPM-A1B2 --guidance "Updated command..." --json
```

## 2. What NEVER to Save

* Personal style, tone, verbosity, or local IDE preferences (`category: user` is rejected).
* Facts directly derivable from source code, Git log, or existing `eidos/` specs.
* Architectural boundaries, contracts, or permanent rules—record those as `DEC-*`, `INV-*`, or `POL-*`.
* One-off debugging notes for a localized typo.

## 3. Freshness, Advisory Surfacing, Audit & Promotion

* `eidos context` attaches relevant active memories with `freshness` (`fresh`, `stale`, `expired`, `unverifiable`) and compact `guidance` on `guardrails`/`deep`.
* `eidos drift` surfaces diff-scoped `affected_memories` and `eidos doctor` reports `checks.team_memories` (both strictly non-blocking).
* Before trusting a `stale`, `expired`, or `unverifiable` memory, verify against live code, then run `eidos memory verify <ID>`, `eidos memory update <ID>`, or `eidos memory archive <ID>`.
* Inspect or audit notes via `eidos memory list --json`, `eidos memory search "<query>" --json`, `eidos memory show <ID> --json`, and `eidos memory audit --json` (always exit `0`).
* Graduate proven lessons into formal `DEC-*`, `INV-*`, or `POL-*` specs first, then run `eidos memory promote <ID> --to <decision|invariant|policy> --spec-id <SPEC-ID>`.
