# Root cause: typed numbers

## Scope

Text typed as a number that is read differently by field, by browser
language and by which side reads it.

This file is one pull request. It holds one package, `FES-06`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `RTG-03` | [rating](rating.md) | A decimal comma is not dropped from a relativity or a boundary | Resolves |
| `WB-48` | [workbench](workbench.md) | A comma out of place is not dropped from a typed number | Resolves |
| `WB-68` | [workbench](workbench.md) | A number in a params file is read as the same number typed in a cell | Resolves |
| `EXE-02` | [execution-engine](execution-engine.md) | A Constant's value runs as the type its settings file holds | Resolves |
| `WB-62` | [workbench](workbench.md) | A whole number of more than 15 digits is shown as typed in a formatted column | In part |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FES-06 | Planned | P2 | The same keystrokes give the same number in every number field, in any browser language, and on the server. |

## Planned improvements

### FES-06 — One rule reads a number that was typed
**Why:** Read on 16 November 2026. The Workbench has one rule for a typed
number, kept the same on the sheet and the server on purpose
(`parseTypedNumber` and its server twin), and `WB-48` records what that
rule gets wrong. Everywhere else each field reads its own text. The
editor's panels, nodes and components call the language's own number
parsing 212 times in 62 files, so `1,5` in a Rating Step's Default is 15
in an English browser and 1.5 in a German one (`RTG-03`). On the server a
Constant's value goes through Python's `float`, which takes `0123`, `1E5`
and `nan` (`EXE-02`), and a params file refuses `1,000` where a cell of
the same type accepts it (`WB-68`). The same keystrokes give different
numbers by field, by browser language and by which side reads them.

**Plan:** State one rule: which thousands separators, decimal marks,
signs, spaces and exponent forms are a number, with text that could be two
numbers refused and never guessed. Keep it as one file of cases, text to
number or refusal, that the browser's reader and the server's reader are
both tested against. Give every number field the one shared field, and
read a Constant's value and a params file by the same rule. Add a test
that lists the input handlers which parse a number themselves, and only
lets the list shrink.

**Acceptance:** The file of cases holds at least `1,5`, `1,000`,
`1 000,50`, `0123`, `1E5`, `1_000`, `nan` and a number of 19 digits, and
both readers pass it. A browser test types each case into every number
field of the editor and the Workbench under English and under German and
requires the same saved value. The acceptance of `RTG-03`, `WB-48`, `WB-68`
and `EXE-02` is met.

**Dependencies:** None. Deliver before the packages its acceptance names.

**Evidence:** `frontend/src/utils/sheetValues.ts`;
`src/haute/_workbench_tables.py`; `src/haute/_node_apply.py`;
`src/haute/_workbench_params.py`;
`frontend/src/components/form/CommittedTextField.tsx`.
