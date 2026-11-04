<!-- eidos:begin agents-md sha256:db57af718386 targets=project:.agents/plugins/eidos/skills · managed by `eidos plugin`; edits inside this block are overwritten -->
## Eidos Living Specifications

This repository is governed by Eidos living specifications in `eidos/`: C4 architecture, domains, capabilities, policies, and test-bound invariants.

- Living specs are the single source of truth. Never write ADRs: record decisions in the owning spec's `decisions:` block and change architecture through SpecDeltas (`eidos/deltas/`).
- Before editing a file, run `eidos context -f <file> --json` to see its owning component, invariants (`INV-*`), decisions (`DEC-*`), and contracts.
- For discovery, prefer `eidos index find-symbol`, `eidos index search`, and `eidos index blast-radius` over raw grep.
- Bind every invariant to a test (`test_binding: path::func`) that mentions its `INV-*` ID.
- Before finishing, run `eidos validate --json` and `eidos drift --uncommitted --json`, and fix every finding.

Load the Eidos skills for detailed workflows: `eidos` (core workflow), `eidos-spec` (authoring specs), `eidos-plan` (SpecDeltas, explorations, reviews), and `eidos-ops` (setup, brownfield extraction, portal). Installed at:

- `.agents/plugins/eidos/skills/<skill>/SKILL.md`

<!-- eidos:end agents-md -->
