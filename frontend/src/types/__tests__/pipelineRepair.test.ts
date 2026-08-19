import { describe, expect, it } from "vitest"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import {
  parseRemoveUnavailableNodeApplyResponse,
  parseRemoveUnavailableNodeDryRunResponse,
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
}

describe("pipeline repair response parsers", () => {
  it("accepts the exact remove-only wire responses", () => {
    expect(parseRemoveUnavailableNodeDryRunResponse(plan)).toEqual(plan)
    expect(parseRemoveUnavailableNodeApplyResponse({
      repair_kind: "remove_unavailable_node",
      plan_hash: hash,
      applied_artifacts: ["main.py"],
      document: makePipelineEditorDocument(),
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
})
