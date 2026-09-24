# Modelling roadmap

## Scope

Where the modelling node's evaluation and tuning result invariants are
checked. Current behaviour is specified in
[the modelling specification](../modelling/low-level.md) and
[the modelling UI specification](../frontend-modelling-optimiser-ui/low-level.md).

The GLM terms, regularisation, offset and pane work (`MOD-T00` to `MOD-T08`)
and the model-family expansion (XGBoost, LightGBM, EBM and XGBoost GPU
training) are delivered, and those specifications hold their designs. The
expansion's dated evidence records remain: the
[engine probes](mod-f00-engine-probes.md), the
[CPU release check](mod-f05-release-check.md) and the
[GPU probes](mod-f06-gpu-probes.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| MOD-T10 | Planned | P3 | Evaluation and tuning result invariants are checked once, where the artifacts are produced. |

## Planned improvements

`MOD-T10` comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

### MOD-T10 — Evaluation and tuning invariants are checked once
**Why:** The same semantic invariants on evaluation and tuning results are
checked in three places: modelling writes and strictly reloads the plan and
report artifacts; the response models re-validate them
(`TuningReportPayload._validate_report` is 138 lines with cyclomatic
complexity 53, `EvaluationReportPayload._validate_report` complexity 36); and
the browser checks them again ("selection fits must be contiguous", "tuning
trials must be contiguous", "start with one empty baseline"). Each invariant
change has to be made in Python twice and in TypeScript once.

**Plan:** Keep the semantic checks where the artifacts are produced and
reloaded. Reduce the response models to structural validation, generated for
the browser by `API-R03`, and delete the browser's semantic re-checks.

**Acceptance:** Each evaluation and tuning invariant has one implementation
and one test; the response models and the browser parser validate structure
only; the existing modelling suites pass.

**Dependencies:** `API-R03` (server API) for the generated browser parser.

**Evidence:** `src/haute/schemas.py::TuningReportPayload`;
`src/haute/schemas.py::EvaluationReportPayload`;
`src/haute/modelling/_evaluation.py`; `src/haute/modelling/_tuning.py`;
`frontend/src/types/trainGuards.ts::parseTrainResponse`.
