project

<!-- eidos:begin agents-md sha256:dbd6d2dff8ab targets=antigravity:.agents/skills · managed by `eidos plugin`; edits inside this block are overwritten -->
## Eidos Living Specifications

This repository is governed by Eidos living specifications in `eidos/` (or `<subproject>/eidos/` in monorepos): C4 architecture, domains, capabilities, personas, policies, design systems/clients, and test-bound invariants.

- **Monorepos with multiple `eidos/` directories**: always scope commands to the target sub-project via `-p <subproject>/eidos` (or run inside `<subproject>`); never rely on root queries for sub-project code.
- Living specs are the single source of truth. Never write ADRs: record decisions in the owning spec's `decisions:` block and change architecture through SpecDeltas (`eidos/deltas/`).
- Before editing a file, run `eidos context -f <file> --json` for its owning component, domain (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), policies (`POL-*`), design (`DS-*`, `CLT-*`), invariants (`INV-*`), decisions (`DEC-*`), and contracts (`CTR-*`)—co-evolve every affected layer (trust its output without re-reading `.adoc` files).
- Prefer `eidos index find-symbol`, `eidos index search`, and `eidos index blast-radius` over raw grep; pass returned `location.file` and `line_start`..`line_end` to `read_file`.
- Bind every invariant to a test (`test_binding: path::func`) mentioning its `INV-*` ID.
- Before finishing, run `eidos drift --uncommitted --json`, do an adversarial `git diff` review (probing false positives, sibling callers, and edge cases; fix every finding), and save/update shared team memory via `eidos memory add` (`--file`/`--from-diff`) or `eidos memory update` for any user correction or non-obvious gotcha/bug root cause (note `MEM-*` in your reply; never personal prefs).

Load Eidos skills for detailed workflows: `eidos` (core), `eidos-spec` (authoring), `eidos-plan` (SpecDeltas, explorations, reviews), `eidos-ops` (setup, brownfield, portal), and `eidos-memory` (team memory). Installed at:

- `.agents/skills/<skill>/SKILL.md`

<!-- eidos:end agents-md -->
