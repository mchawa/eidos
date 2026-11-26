---
name: eidos-memory
description: "Git-native team operational memory (eidos/memory/mem-*.adoc). Load when corrected on a repo workflow, test/build quirk, or environment constraint, when reconciling stale/expired memories surfaced in eidos context, or when auditing or promoting team notes into formal DEC-*/INV-*/POL-* specs."
---
# Eidos Team Auto-Memory (`eidos-memory`)

`eidos/memory/` stores **shared, Git-tracked team operational memory** (`type: agent/memory`, one `mem-*.adoc` file per entry) across all coding agents. Personal developer preferences never belong here.

## 1. When to Save Automatically (and Disclose)

Run `eidos memory add` proactively—and explicitly report the created or updated `MEM-*` file path in your reply so the engineer can review or revert it in Git—whenever:
* The user corrects a repo-specific engineering assumption, test/build convention, toolchain quirk, or workflow mistake (`--category feedback`, no TTL).
* You uncover a **time-bounded** operational constraint, release freeze, CI cap, or active migration caveat (`--category project --expires YYYY-MM-DD`).
* The user points to a shared operational dashboard, staging URL, or benchmark resource (`--category reference`).

Prefer anchoring `--entity <CNT-*|CMP-*|DOM-*|CAP-*>` and/or `--file <path_or_glob>` so memories attach automatically in `eidos context` instead of becoming untargeted global notes:

```bash
eidos memory add --title "Use pnpm in web frontend" --category feedback \
  --summary "Run pnpm inside apps/web; npm fails on package.json devEngines." \
  --guidance "Use pnpm exec vitest and pnpm build inside apps/web." \
  --why "package.json devEngines pins pnpm." \
  --entity CMP-WEB-APP --file "apps/web/**" --json
```

## 2. What NEVER to Save

* Personal style, tone, verbosity, or local IDE preferences (leave those to client-native personal memory; `category: user` is rejected).
* Facts directly derivable from source code, Git log, or existing `eidos/` specs.
* Architectural boundaries, contracts, or permanent rules—record those as `DEC-*`, `INV-*`, or `POL-*`.
* One-off debugging notes for a localized typo.

## 3. Freshness, Audit & Promotion

* `eidos context` attaches relevant active memories with a computed `freshness` tag (`fresh`, `stale`, `expired`, `unverifiable`).
* Before trusting a `stale`, `expired`, or `unverifiable` memory, verify it against live code, then run `eidos memory verify <ID>` or `eidos memory archive <ID>`.
* Inspect or search existing notes with `eidos memory list --json`, `eidos memory search "<query>" --json`, and `eidos memory show <ID> --json`.
* Run `eidos memory audit --json` periodically to reconcile stale targets, expired notes, duplicates, and spec overlaps (non-blocking).
* Graduate proven lessons into formal `DEC-*`, `INV-*`, or `POL-*` specs first, then mark the memory promoted with `eidos memory promote <ID> --to <decision|invariant|policy> --spec-id <SPEC-ID>`.
