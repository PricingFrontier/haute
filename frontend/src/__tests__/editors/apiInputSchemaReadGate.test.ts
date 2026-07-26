/**
 * Render-gate contract for `readV2` (the v2 apiInput read codec).
 *
 * Bug class (silent data loss). `readV2` historically did
 * `if (!path) continue` for tables and `if (!cname || !cpath) continue`
 * for columns — silently dropping any persisted entry whose name/path
 * was blank. Because the editor re-derives its view through
 * `classifyConfig → readV2` and then re-serialises that view via
 * `writeV2` on the next edit, a dropped-on-read entry was permanently
 * deleted from the persisted config on the user's next keystroke
 * elsewhere. A blank entry can arrive from a hand-edited current file or an
 * interrupted edit. Either way the user needs a surface to see or repair it —
 * a violation of the 1:1 JSON↔UI render-gate invariant (every persisted
 * entry must surface somewhere visible; greying/erroring is fine,
 * suppression is not).
 *
 * Fix. `readV2` KEEPS such entries by default (the disk/render path);
 * the editor renders them in an invalid state (inline error) so they
 * can be repaired or explicitly deleted. The Infer-Tables path — fresh
 * backend output that was never user-persisted — opts back into dropping
 * via `{ dropIncomplete: true }`.
 *
 * Companion suites: `ApiInputEditor.test.tsx` (the editor surfaces these entries + the
 * persistent-boundary round-trip), and `tests/test_v2_codec_and_shred.py`
 * (the backend twin LOUDLY rejects the same blanks rather than dropping).
 *
 * Paths use the canonical `[:]` array selector (the only form accepted by
 * the path grammar); `readV2` itself is grammar-agnostic and passes paths
 * through verbatim, so these fixtures only assert keep/drop behaviour.
 */
import { describe, it, expect } from "vitest"

import { readV2, writeV2 } from "../../panels/editors/apiInputSchema"

/** A column whose source path is intact but whose name was cleared. */
const BLANK_NAME_COL = {
  name: "",
  path: "$[:].policy_id",
  type: "int",
  status: "Inferred",
  selected: true,
  levels: null,
}

/** A column whose name is intact but whose source path was cleared. */
const BLANK_PATH_COL = {
  name: "premium",
  path: "",
  type: "float",
  status: "Inferred",
  selected: true,
  levels: null,
}

const VALID_COL = {
  name: "policy_ref",
  path: "$[:].policy_ref",
  type: "str",
  status: "Confirmed",
  selected: true,
  levels: null,
}

function configWith(columns: unknown[]): Record<string, unknown> {
  return {
    path: "data/input.json",
    contract: "opaque",
    tables: [
      { path: "$[:]", label: "policies", emit: true, columns },
    ],
  }
}

describe("readV2 render-gate — keeps blank entries by default (no silent drop)", () => {
  it("keeps a column whose NAME is blank (path intact)", () => {
    const v2 = readV2(configWith([BLANK_NAME_COL, VALID_COL]))
    expect(v2.tables[0].columns).toHaveLength(2)
    expect(v2.tables[0].columns[0]).toMatchObject({ name: "", path: "$[:].policy_id" })
  })

  it("keeps a column whose PATH is blank (name intact)", () => {
    const v2 = readV2(configWith([BLANK_PATH_COL, VALID_COL]))
    expect(v2.tables[0].columns).toHaveLength(2)
    expect(v2.tables[0].columns[0]).toMatchObject({ name: "premium", path: "" })
  })

  it("keeps a column whose name AND path are both blank", () => {
    const v2 = readV2(configWith([{ name: "", path: "", type: "str" }, VALID_COL]))
    expect(v2.tables[0].columns).toHaveLength(2)
    expect(v2.tables[0].columns[0]).toMatchObject({ name: "", path: "" })
  })

  it("keeps a table whose PATH is blank", () => {
    const config = {
      tables: [
        { path: "", label: "orphan", emit: true, columns: [] },
        { path: "$[:]", label: "policies", emit: true, columns: [] },
      ],
    }
    const v2 = readV2(config)
    expect(v2.tables).toHaveLength(2)
    expect(v2.tables[0]).toMatchObject({ path: "", label: "orphan" })
  })

  it("keeps a blank table LABEL verbatim — does NOT coerce it to the path", () => {
    // The label is the runtime port name; a blank label is backend-invalid.
    // Masking it as the path would render the row as valid and silently
    // rewrite the persisted "" to the path on the next edit.
    const v2 = readV2({
      tables: [{ path: "$[:]", label: "", emit: true, columns: [] }],
    })
    expect(v2.tables).toHaveLength(1)
    expect(v2.tables[0].label).toBe("")
  })

  it("defaults a MISSING label key to the path (inference convention preserved)", () => {
    // Distinct from a blank label: an omitted label is the inference
    // convention (label defaults to the table path), not a user-cleared
    // field, so coercion is correct there.
    const v2 = readV2({
      tables: [{ path: "$[:]", emit: true, columns: [] }],
    })
    expect(v2.tables[0].label).toBe("$[:]")
  })

  it("coerces a missing name/path to '' but still KEEPS the entry", () => {
    // A column object lacking the keys entirely (not even blank strings)
    // is still a content-bearing persisted entry — surface it, don't drop.
    const v2 = readV2(configWith([{ type: "int", selected: true }, VALID_COL]))
    expect(v2.tables[0].columns).toHaveLength(2)
    expect(v2.tables[0].columns[0]).toMatchObject({ name: "", path: "" })
  })

  it("preserves the 1:1 entry count for a config full of blanks", () => {
    const v2 = readV2(configWith([BLANK_NAME_COL, BLANK_PATH_COL, VALID_COL]))
    expect(v2.tables[0].columns).toHaveLength(3)
  })

  it("still drops genuinely non-object entries (null / string) — not content-bearing", () => {
    const config = {
      tables: [
        null,
        "garbage",
        { path: "$[:]", label: "policies", emit: true, columns: [null, "x", VALID_COL] },
      ],
    }
    const v2 = readV2(config)
    expect(v2.tables).toHaveLength(1)
    expect(v2.tables[0].columns).toHaveLength(1)
    expect(v2.tables[0].columns[0]).toMatchObject({ name: "policy_ref" })
  })

  it("round-trips a blank entry through writeV2 without losing it", () => {
    // The data-loss mechanism was read→re-serialise dropping the entry.
    // With the keep behaviour, writeV2(readV2(x)) must retain the blank.
    const raw = writeV2(readV2(configWith([BLANK_NAME_COL, VALID_COL])))
    const cols = (raw.tables as Array<{ columns: unknown[] }>)[0].columns
    expect(cols).toHaveLength(2)
    expect(cols[0]).toMatchObject({ name: "", path: "$[:].policy_id" })
  })

})

describe("readV2 { dropIncomplete: true } — infer-path sanitisation drops blanks", () => {
  it("drops blank-name and blank-path columns", () => {
    const v2 = readV2(configWith([BLANK_NAME_COL, BLANK_PATH_COL, VALID_COL]), {
      dropIncomplete: true,
    })
    expect(v2.tables[0].columns).toHaveLength(1)
    expect(v2.tables[0].columns[0]).toMatchObject({ name: "policy_ref" })
  })

  it("drops blank-path tables", () => {
    const config = {
      tables: [
        { path: "", label: "orphan", emit: true, columns: [] },
        { path: "$[:]", label: "policies", emit: true, columns: [] },
      ],
    }
    const v2 = readV2(config, { dropIncomplete: true })
    expect(v2.tables).toHaveLength(1)
    expect(v2.tables[0]).toMatchObject({ path: "$[:]", label: "policies" })
  })

  it("drops blank-label tables (symmetric with blank path/name)", () => {
    const config = {
      tables: [
        { path: "$[:].a[:]", label: "", emit: true, columns: [] },
        { path: "$[:]", label: "policies", emit: true, columns: [] },
      ],
    }
    const v2 = readV2(config, { dropIncomplete: true })
    expect(v2.tables).toHaveLength(1)
    expect(v2.tables[0]).toMatchObject({ path: "$[:]", label: "policies" })
  })
})
