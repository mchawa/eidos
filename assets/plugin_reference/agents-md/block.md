## Eidos Living Specifications

This repository is governed by Eidos living specifications in `eidos/`: C4 architecture, domains, capabilities, personas, policies, design systems/clients, and test-bound invariants.

- Living specs are the single source of truth. Never write ADRs: record decisions in the owning spec's `decisions:` block and change architecture through SpecDeltas (`eidos/deltas/`).
- Before editing a file, run `eidos context -f <file> --json` to see its owning component, domain (`DOM-*`), capabilities (`CAP-*`), personas (`PER-*`), policies (`POL-*`), design (`DS-*`, `CLT-*`), invariants (`INV-*`), decisions (`DEC-*`), and contracts (`CTR-*`)—co-evolve every affected layer (trust its output directly without re-reading summarized `.adoc` files).
- For discovery, prefer `eidos index find-symbol`, `eidos index search`, and `eidos index blast-radius` over raw grep, and pass the returned `location.file` and `line_start`..`line_end` to `read_file` instead of reading full files.
- Bind every invariant to a test (`test_binding: path::func`) that mentions its `INV-*` ID.
- When corrected or after hitting a non-obvious build/test/CI/tooling gotcha, save shared team memory via `eidos memory add` (auto-anchors `--file` / `--from-diff`) and note the `MEM-*` file in your reply (never personal prefs).
- Before finishing, run `eidos drift --uncommitted --json` and an adversarial `git diff` review (probing heuristic false positives, sibling callers, and edge cases; fix every finding before responding).

Load the Eidos skills for detailed workflows: `eidos` (core workflow), `eidos-spec` (authoring specs), `eidos-plan` (SpecDeltas, explorations, reviews), `eidos-ops` (setup, brownfield extraction, portal), and `eidos-memory` (shared team operational memory). Installed at:

{{skill_locations}}

