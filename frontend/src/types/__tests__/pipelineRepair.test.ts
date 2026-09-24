import { describe, expect, it } from "vitest"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import {
  parseRemoveUnavailableNodeApplyResponse,
  parseRecoverUnavailableNodeApplyResponse,
} from "../pipelineRepair"

const change = { path: "main.py", operation: "update", description: "Remove broken.", diff: "-broken", diff_truncated: false }

function applied(overrides: Record<string, unknown> = {}) {
  return {
    repair_kind: "remove_unavailable_node",
    applied_artifacts: ["main.py"],
    changes: [change],
    document: makePipelineEditorDocument(),
    field_changes: [],
    completeness: [],
    previous_config: null,
    ...overrides,
  }
}

describe("pipeline repair response parsers", () => {
  it("accepts the exact remove-node apply response with its display diffs", () => {
    const parsed = parseRemoveUnavailableNodeApplyResponse(applied())
    expect(parsed.document.document_kind).toBe("haute.pipeline_editor_document")
    expect(parsed.changes).toEqual([change])
    expect(parseRemoveUnavailableNodeApplyResponse(applied({
      changes: [{ ...change, description: "😀".repeat(1024) }],
    })).changes[0].description).toHaveLength(2048)
  })

  it("rejects malformed values, extra keys and a plan hash", () => {
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({ plan_hash: "a".repeat(64) }))).toThrow("unexpected or missing fields")
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({ unexpected: true }))).toThrow("unexpected")
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({ applied_artifacts: ["main.py", "main.py"] }))).toThrow("duplicate")
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({ changes: [] }))).toThrow("changes")
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({ changes: [change, change] }))).toThrow("duplicate")
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({
      changes: [{ ...change, description: "x".repeat(1025) }],
    }))).toThrow("description")
    expect(() => parseRemoveUnavailableNodeApplyResponse(applied({
      changes: [{ ...change, diff: "x".repeat(131_073) }],
    }))).toThrow("diff")
  })

  it("accepts update/reset recovery responses and rejects the removal discriminator", () => {
    expect(parseRecoverUnavailableNodeApplyResponse(applied({ repair_kind: "reset_node" })).repair_kind).toBe("reset_node")
    expect(parseRecoverUnavailableNodeApplyResponse(applied({ repair_kind: "update_node" })).repair_kind).toBe("update_node")
    expect(() => parseRecoverUnavailableNodeApplyResponse(applied())).toThrow("repair_kind")
  })

  it("accepts recover responses carrying the field-outcome report", () => {
    const recover = applied({
      repair_kind: "recover_node",
      field_changes: [
        { path: "/path", outcome: "retained", reason: "Valid under the current configuration contract." },
        { path: "/cacheMode", outcome: "removed", reason: "Unknown or retired configuration field retained only in recovery evidence." },
      ],
      completeness: [
        { element_id: "broken@10", path: "path", code: "required", message: "Format 'parquet' requires a non-empty 'path'." },
      ],
      previous_config: { inputType: "file", cacheMode: "snapshot" },
    })
    const parsed = parseRecoverUnavailableNodeApplyResponse(recover)
    expect(parsed.repair_kind).toBe("recover_node")
    expect(parsed.field_changes).toHaveLength(2)
    expect(parsed.completeness[0].path).toBe("path")
    expect(parsed.previous_config).toEqual({ inputType: "file", cacheMode: "snapshot" })
    expect(() =>
      parseRecoverUnavailableNodeApplyResponse({
        ...recover,
        field_changes: [{ path: "/x", outcome: "vanished", reason: "?" }],
      }),
    ).toThrow("outcome")
  })
})
