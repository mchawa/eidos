project

<!-- eidos:begin agents-md sha256:34cad06ff1a8 targets=antigravity:.agents/skills · managed by `eidos plugin`; edits inside this block are overwritten -->
## Eidos Living Specifications

This repository is governed by Eidos living specifications in `eidos/` (or `<subproject>/eidos/` in monorepos): C4 architecture, domains, capabilities, personas, policies, design systems/clients, and test-bound invariants.

- **Monorepos with multiple `eidos/` directories**: always scope commands to the target sub-project via `-p <subproject>/eidos` (or run inside `<subproject>`); never rely on root queries for sub-project code.
- Living specs are the single source of truth. Never write ADRs: record decisions in the owning spec's `decisions:` block and change architecture through SpecDeltas (`eidos/deltas/`).
- Before editing a file, run `eidos context -f <file> --json` for its owning component, domain (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), policies (`POL-*`), design (`DS-*`, `CLT-*`), invariants (`INV-*`), decisions (`DEC-*`), and contracts (`CTR-*`)—co-evolve every affected layer (trust its output without re-reading `.adoc` files).
- Prefer `eidos index find-symbol`, `eidos index search`, and `eidos index blast-radius` over raw grep; pass returned `location.file` and `line_start`..`line_end` to `read_file`.
- Bind every invariant to a test (`test_binding: path::func`) mentioning its `INV-*` ID.
- When corrected or after hitting a non-obvious build/test/CI/tooling gotcha, save shared team memory via `eidos memory add` (`--file` / `--from-diff`) and note the `MEM-*` file in your reply (never personal prefs).
- Before finishing, run `eidos drift --uncommitted --json` and an adversarial `git diff` review (probing heuristic false positives, sibling callers, and edge cases; fix every finding before responding).

Load Eidos skills for detailed workflows: `eidos` (core), `eidos-spec` (authoring), `eidos-plan` (SpecDeltas, explorations, reviews), `eidos-ops` (setup, brownfield, portal), and `eidos-memory` (team memory). Installed at:

- `.agents/skills/<skill>/SKILL.md`

<!-- eidos:end agents-md -->
