import { describe, expect, it } from "vitest"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import {
  parseRemoveBrokenConnectionApplyResponse,
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

const INVALID = "PipelineRepairApplyResponse: invalid contract at"

describe("pipeline repair response parsers", () => {
  it("accepts the exact remove-node apply response with its display diffs", async () => {
    const parsed = await parseRemoveUnavailableNodeApplyResponse(applied())
    expect(parsed.document.document_kind).toBe("haute.pipeline_editor_document")
    expect(parsed.changes).toEqual([change])
    // A description's 1,024-character bound counts characters, not UTF-16 units.
    const emoji = await parseRemoveUnavailableNodeApplyResponse(applied({
      changes: [{ ...change, description: "😀".repeat(1024) }],
    }))
    expect(emoji.changes[0].description).toHaveLength(2048)
  })

  it.each([
    [{ plan_hash: "a".repeat(64) }, `${INVALID} /: additionalProperties`],
    [{ changes: [] }, `${INVALID} /changes: minItems`],
    [{ changes: [{ ...change, description: "x".repeat(1025) }] }, `${INVALID} /changes/0/description: maxLength`],
    [{ changes: [{ ...change, diff: "x".repeat(131_073) }] }, `${INVALID} /changes/0/diff: maxLength`],
    [{ applied_artifacts: ["main.py", "main.py"] }, "response.applied_artifacts contains duplicate values"],
    [{ changes: [change, change] }, "response.changes paths contains duplicate values"],
  ])("rejects the malformed response %o", async (overrides, message) => {
    await expect(parseRemoveUnavailableNodeApplyResponse(applied(overrides))).rejects.toThrow(message)
  })

  it("accepts a connection removal response and only that discriminator", async () => {
    expect((await parseRemoveBrokenConnectionApplyResponse(applied({ repair_kind: "remove_connection" }))).repair_kind)
      .toBe("remove_connection")
    await expect(parseRemoveBrokenConnectionApplyResponse(applied())).rejects.toThrow("repair_kind")
    await expect(parseRemoveUnavailableNodeApplyResponse(applied({ repair_kind: "remove_connection" })))
      .rejects.toThrow("repair_kind")
  })

  it("accepts reset recovery responses and rejects the removal and retired update discriminators", async () => {
    expect((await parseRecoverUnavailableNodeApplyResponse(applied({ repair_kind: "reset_node" }))).repair_kind)
      .toBe("reset_node")
    await expect(parseRecoverUnavailableNodeApplyResponse(applied({ repair_kind: "update_node" })))
      .rejects.toThrow("repair_kind")
    await expect(parseRecoverUnavailableNodeApplyResponse(applied())).rejects.toThrow("repair_kind")
  })

  it("accepts recover responses carrying the field-outcome report", async () => {
    const recover = applied({
      repair_kind: "recover_node",
      field_changes: [
        { path: "/path", outcome: "retained", reason: "Valid under the current configuration contract." },
        { path: "/cacheMode", outcome: "removed", reason: "Unknown or retired configuration field retained only in recovery evidence." },
      ],
      completeness: [
        { element_id: "broken@10", path: "path", code: "required", message: "Format 'parquet' requires a non-empty 'path'." },
      ],
      previous_config: { input_type: "file", cacheMode: "snapshot" },
    })
    const parsed = await parseRecoverUnavailableNodeApplyResponse(recover)
    expect(parsed.repair_kind).toBe("recover_node")
    expect(parsed.field_changes).toHaveLength(2)
    expect(parsed.completeness[0].path).toBe("path")
    expect(parsed.previous_config).toEqual({ input_type: "file", cacheMode: "snapshot" })
    await expect(
      parseRecoverUnavailableNodeApplyResponse({
        ...recover,
        field_changes: [{ path: "/x", outcome: "vanished", reason: "?" }],
      }),
    ).rejects.toThrow(`${INVALID} /field_changes/0/outcome: enum`)
  })
})
