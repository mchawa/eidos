## Eidos Living Specifications

This repository is governed by Eidos living specifications in `eidos/`: C4 architecture, domains, capabilities, policies, and test-bound invariants.

- Living specs are the single source of truth. Never write ADRs: record decisions in the owning spec's `decisions:` block and change architecture through SpecDeltas (`eidos/deltas/`).
- Before editing a file, run `eidos context -f <file> --json` to see its owning component, invariants (`INV-*`), decisions (`DEC-*`), and contracts (trust its output directly without re-reading the summarized `.adoc` files).
- For discovery, prefer `eidos index find-symbol`, `eidos index search`, and `eidos index blast-radius` over raw grep, and pass the returned `location.file` and `line_start`..`line_end` to `read_file` instead of reading full files.
- Bind every invariant to a test (`test_binding: path::func`) that mentions its `INV-*` ID.
- Before finishing, run `eidos drift --uncommitted --json` and fix every finding.

Load the Eidos skills for detailed workflows: `eidos` (core workflow), `eidos-spec` (authoring specs), `eidos-plan` (SpecDeltas, explorations, reviews), and `eidos-ops` (setup, brownfield extraction, portal). Installed at:

{{skill_locations}}

