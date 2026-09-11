import { describe, expect, it } from "vitest"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import {
  parseRemoveUnavailableNodeApplyResponse,
  parseRemoveUnavailableNodeDryRunResponse,
  parseRecoverUnavailableNodeApplyResponse,
  parseRecoverUnavailableNodeDryRunResponse,
} from "../pipelineRepair"

const hash = "a".repeat(64)
const plan = {
  repair_kind: "remove_unavailable_node",
  source_file: "main.py",
  source_revision: "revision-1",
  target_source_file: "main.py",
  target_recovery_id: "broken@10",
  target_authored_id: "broken",
  delete_config: false,
  plan_hash: hash,
  changes: [{ path: "main.py", operation: "update", description: "Remove broken.", diff: "-broken", diff_truncated: false }],
  retained_artifacts: ["config/broken.json"],
  warnings: ["Config retained."],
  predicted_load_status: "ready",
  field_changes: [],
  completeness: [],
  previous_config: null,
}

describe("pipeline repair response parsers", () => {
  it("accepts the exact remove-only wire responses", () => {
    expect(parseRemoveUnavailableNodeDryRunResponse(plan)).toEqual(plan)
    expect(parseRemoveUnavailableNodeApplyResponse({
      repair_kind: "remove_unavailable_node",
      plan_hash: hash,
      applied_artifacts: ["main.py"],
      document: makePipelineEditorDocument(),
      field_changes: [],
      completeness: [],
      previous_config: null,
    }).document.document_kind).toBe("haute.pipeline_editor_document")
    expect(parseRemoveUnavailableNodeDryRunResponse({
      ...plan,
      changes: [{ ...plan.changes[0], description: "😀".repeat(1024) }],
    }).changes[0].description).toHaveLength(2048)
  })

  it("rejects malformed values and extra keys", () => {
    expect(() => parseRemoveUnavailableNodeDryRunResponse({ ...plan, plan_hash: "A".repeat(64) })).toThrow("plan_hash")
    expect(() => parseRemoveUnavailableNodeDryRunResponse({ ...plan, unexpected: true })).toThrow("unexpected")
    expect(() => parseRemoveUnavailableNodeApplyResponse({
      repair_kind: "remove_unavailable_node", plan_hash: hash, applied_artifacts: ["main.py", "main.py"], document: makePipelineEditorDocument(),
      field_changes: [], completeness: [], previous_config: null,
    })).toThrow("duplicate")
    expect(() => parseRemoveUnavailableNodeDryRunResponse({
      ...plan,
      changes: [{ ...plan.changes[0], description: "x".repeat(1025) }],
    })).toThrow("description")
    expect(() => parseRemoveUnavailableNodeDryRunResponse({
      ...plan,
      changes: [{ ...plan.changes[0], diff: "x".repeat(131_073) }],
    })).toThrow("diff")
  })

  it("accepts update/reset recovery responses but rejects config deletion", () => {
    const update = { ...plan, repair_kind: "update_node", delete_config: false }
    expect(parseRecoverUnavailableNodeDryRunResponse(update).repair_kind).toBe("update_node")
    expect(parseRecoverUnavailableNodeApplyResponse({ repair_kind: "reset_node", plan_hash: hash, applied_artifacts: ["main.py"], document: makePipelineEditorDocument(), field_changes: [], completeness: [], previous_config: null }).repair_kind).toBe("reset_node")
    expect(() => parseRecoverUnavailableNodeDryRunResponse({ ...update, delete_config: true })).toThrow("delete_config")
    expect(() => parseRecoverUnavailableNodeDryRunResponse({ ...update, repair_kind: "remove_unavailable_node" })).toThrow("repair_kind")
  })

  it("accepts recover responses carrying the field-outcome report", () => {
    const recover = {
      ...plan,
      repair_kind: "recover_node",
      field_changes: [
        { path: "/path", outcome: "retained", reason: "Valid under the current configuration contract." },
        { path: "/cacheMode", outcome: "removed", reason: "Unknown or retired configuration field retained only in recovery evidence." },
      ],
      completeness: [
        { element_id: "broken@10", path: "path", code: "required", message: "Format 'parquet' requires a non-empty 'path'." },
      ],
      previous_config: { inputType: "file", cacheMode: "snapshot" },
    }
    const parsed = parseRecoverUnavailableNodeDryRunResponse(recover)
    expect(parsed.repair_kind).toBe("recover_node")
    expect(parsed.field_changes).toHaveLength(2)
    expect(parsed.completeness[0].path).toBe("path")
    expect(parsed.previous_config).toEqual({ inputType: "file", cacheMode: "snapshot" })
    expect(() =>
      parseRecoverUnavailableNodeDryRunResponse({
        ...recover,
        field_changes: [{ path: "/x", outcome: "vanished", reason: "?" }],
      }),
    ).toThrow("outcome")
  })
})
