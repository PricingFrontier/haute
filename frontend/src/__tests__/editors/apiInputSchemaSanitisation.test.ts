/**
 * Contract tests for `removedTables` sanitisation.
 *
 * Background. `removedTables` was a v2 TypedDict field meant as
 * "an editor-side ledger of labels the user deleted (so a Re-Infer
 * doesn't resurrect them)" — but the inferTables handler at
 * `ApiInputEditor.tsx:241` clobbers tables without consulting
 * `removedTables`. The feature was specified but never wired.
 *
 * Per Nick's directive: user deletion of tables should NOT permanently
 * alter Infer Tables behaviour. Sanitisation is the fix: drop the field
 * from TypeScript + Python type declarations, drop from readV2/writeV2,
 * and silently ignore it on load if present in older on-disk configs.
 *
 * Companion: `tests/test_config_validation.py` asserts the Python
 * TypedDict mirror is also clean.
 *
 * If we ever want to implement "remember deleted tables" as a feature,
 * it would be a new design — not a resurrection of this dead ledger.
 */
import { describe, it, expect } from "vitest"

import {
  readV2,
  writeV2,
  type ApiInputConfigV2,
} from "../../panels/editors/apiInputSchema"

describe("apiInputSchema — removedTables sanitisation", () => {
  it("writeV2 output never carries `removedTables`, even when input has it (cast)", () => {
    // Cast through `unknown` — once `ApiInputConfigV2` no longer declares
    // `removedTables` the compile-time check fires too; this runtime
    // assertion is the load-bearing contract for any in-memory state
    // that *did* once carry the field (e.g. via external tooling or a
    // pre-sanitisation config dict).
    const v2WithDeadField = {
      path: "data.json",
      contract: "opaque",
      tables: [],
      removedTables: ["dropped_a", "dropped_b"],
    } as unknown as ApiInputConfigV2
    const raw = writeV2(v2WithDeadField)
    expect(raw).not.toHaveProperty("removedTables")
  })

  it("readV2 result has no `removedTables` field even when raw config carries it", () => {
    const onDisk: Record<string, unknown> = {
      path: "data.json",
      contract: "opaque",
      tables: [],
      removedTables: ["dropped_a"],
    }
    const v2 = readV2(onDisk)
    expect(v2).not.toHaveProperty("removedTables")
  })

  it("readV2 does not throw on a malformed `removedTables` payload (silent ignore)", () => {
    const onDisk: Record<string, unknown> = {
      path: "data.json",
      tables: [],
      removedTables: "not even an array",
    }
    expect(() => readV2(onDisk)).not.toThrow()
  })

  it("writeV2 output is the documented v2 surface — no `removedTables`, no surprise keys", () => {
    const v2: ApiInputConfigV2 = {
      path: "x.json",
      contract: "opaque",
      tables: [],
    }
    const raw = writeV2(v2)
    // Pinning the exact key set: any new top-level v2 field must be a
    // deliberate spec change, not a silent leak.
    expect(Object.keys(raw).sort()).toEqual(["contract", "path", "tables"])
  })
})
