import { describe, expect, it } from "vitest"
import { adaptPipelineEditorDocument, parsePipelineEditorDocument } from "../pipelineDocument"
import type { PipelineEditorDocument } from "../pipelineDocument"

function document(): PipelineEditorDocument {
  return {
    document_kind: "haute.pipeline_editor_document", schema_version: 1, load_status: "degraded",
    pipeline_name: "Main", pipeline_description: null, preamble: null, preserved_blocks: [], source_file: "main.py",
    source_revision: "abc", source_text: "", sources: ["live"], active_source: "live", source_selection_trusted: true,
    has_authored_content: true, nodes: [{ recovery_id: "node:a", authored_id: "a", label: "A", function_name: "A", default_input_name: "A", source_handle_input_names: {}, decorator_name: "source", node_type: "dataInput", description: "", availability: "ready", display_position: { x: 1, y: 2 }, config: { nested: [1] }, config_reference: null, source_file: null, source_span: null, diagnostic_ids: ["d1"], blocking_path: [] }],
    edges: [], unresolved_connections: [], submodels: null,
    diagnostics: [{ diagnostic_id: "d1", code: "parse_error", severity: "warning", scope: "node", message: "warning", element_id: "node:a", source_file: null, source_span: null, remediation: null, incident_id: null }], diagnostics_omitted: 0,
    capabilities: { can_mutate: false, can_save: false, can_execute: false, can_preview: false, can_manage_submodels: false, can_repair: true, reserved_api_input_frame_labels: ["class", "return"] },
  }
}

describe("pipeline editor document contract", () => {
  it("parses a degraded document and adapts independent canvas metadata", () => {
    const parsed = parsePipelineEditorDocument(document())
    const adapted = adaptPipelineEditorDocument(parsed)
    expect(adapted.nodes[0]).toMatchObject({ id: "node:a", type: "dataInput", data: { _loadAvailability: "ready", _recoveryId: "node:a", _authoredId: "a", _authoredDecorator: "source", _authoredReceiver: "pipeline" } })
    ;(adapted.nodes[0].data.config as { nested: number[] }).nested[0] = 9
    expect((parsed.nodes[0].config?.nested as number[])[0]).toBe(1)
  })

  it("keeps server-owned executable identities and config references on ready nodes and edges", () => {
    const fixture = document()
    fixture.load_status = "ready"
    fixture.nodes[0].label = "API Input"
    fixture.nodes[0].function_name = "API_Input"
    fixture.nodes[0].default_input_name = null
    fixture.nodes[0].source_handle_input_names = { drivers: "drivers" }
    fixture.nodes[0].config_reference = "config/quote_input/API_Input.json"
    fixture.edges = [{
      recovery_id: "edge:drivers",
      source_recovery_id: "node:a",
      target_recovery_id: "node:b",
      source_authored_id: "a",
      target_authored_id: "b",
      source_handle: "drivers",
      target_handle: null,
      source_port: null,
      target_port: null,
      input_name: "drivers",
      availability: "ready",
      source_span: null,
      diagnostic_ids: [],
      blocking_path: [],
    }]
    fixture.nodes.push({
      ...fixture.nodes[0],
      recovery_id: "node:b",
      authored_id: "b",
      label: "Consumer",
      function_name: "Consumer",
      default_input_name: "Consumer",
      source_handle_input_names: {},
      config_reference: null,
    })

    const adapted = adaptPipelineEditorDocument(parsePipelineEditorDocument(fixture))

    expect(adapted.nodes[0].data).toMatchObject({
      _functionName: "API_Input",
      _defaultInputName: null,
      _sourceHandleInputNames: { drivers: "drivers" },
      _configReference: "config/quote_input/API_Input.json",
    })
    expect(adapted.edges[0].data).toMatchObject({ _inputName: "drivers" })
  })

  it("marks submodel-graph nodes with the @submodel authoring receiver", () => {
    const fixture = document()
    fixture.submodels = {
      child: {
        definition_id: "child",
        file: "modules/child.py",
        availability: "ready",
        diagnostic_ids: [],
        graph: {
          nodes: [{ ...fixture.nodes[0], recovery_id: "node:c", authored_id: "c", label: "C", diagnostic_ids: [] }],
          edges: [],
          unresolved_connections: [],
          submodels: null,
        },
        input_ports: [],
        output_ports: [],
      },
    }

    const adapted = adaptPipelineEditorDocument(parsePipelineEditorDocument(fixture))
    expect(adapted.submodels.child.graph.nodes[0]).toMatchObject({
      id: "node:c",
      data: { _authoredReceiver: "submodel" },
    })
  })

  it("renders an unresolved declaration only when both visual endpoints are known", () => {
    const fixture = document()
    fixture.nodes.push({
      ...fixture.nodes[0],
      recovery_id: "node:b",
      authored_id: "b",
      label: "B",
      diagnostic_ids: [],
    })
    fixture.unresolved_connections = [{
      recovery_id: "edge:unresolved",
      source_recovery_id: "node:a",
      target_recovery_id: "node:b",
      source_authored_id: "a",
      target_authored_id: "b",
      source_handle: null,
      target_handle: null,
      source_port: null,
      target_port: null,
      source_span: null,
      diagnostic_ids: ["d1"],
    }]

    const adapted = adaptPipelineEditorDocument(parsePipelineEditorDocument(fixture))
    expect(adapted.edges).toHaveLength(1)
    expect(adapted.edges[0]).toMatchObject({
      id: "edge:unresolved",
      source: "node:a",
      target: "node:b",
      selectable: false,
      data: { _unresolvedConnection: true },
    })

    fixture.unresolved_connections[0].target_recovery_id = null
    expect(
      adaptPipelineEditorDocument(parsePipelineEditorDocument(fixture)).edges,
    ).toHaveLength(0)
  })

  it("supports source_only and rejects shape, coordinates, and edge endpoint drift", () => {
    const sourceOnly = document(); sourceOnly.load_status = "source_only"
    expect(parsePipelineEditorDocument(sourceOnly).load_status).toBe("source_only")
    const extra = document(); Object.assign(extra, { unexpected: true })
    expect(() => parsePipelineEditorDocument(extra)).toThrow(/unexpected or missing/)
    const missing: Partial<PipelineEditorDocument> = document(); delete missing.source_text
    expect(() => parsePipelineEditorDocument(missing)).toThrow(/unexpected or missing/)
    const infinity = document(); infinity.nodes[0].display_position.x = Infinity
    expect(() => parsePipelineEditorDocument(infinity)).toThrow(/finite/)
    const badEdge = document(); badEdge.edges = [{ recovery_id: "edge:1", source_recovery_id: "missing", target_recovery_id: "node:a", source_authored_id: "x", target_authored_id: "a", source_handle: null, target_handle: null, source_port: null, target_port: null, input_name: null, availability: "blocked", source_span: null, diagnostic_ids: [], blocking_path: [] }]
    expect(() => parsePipelineEditorDocument(badEdge)).toThrow(/missing recovery node/)
    const badUnresolved = document(); badUnresolved.unresolved_connections = [{ recovery_id: "edge:unresolved", source_recovery_id: "missing", target_recovery_id: null, source_authored_id: "x", target_authored_id: "a", source_handle: null, target_handle: null, source_port: null, target_port: null, source_span: null, diagnostic_ids: ["d1"] }]
    expect(() => parsePipelineEditorDocument(badUnresolved)).toThrow(/missing recovery node/)
    const badSubmodel = document(); badSubmodel.submodels = { registered: { definition_id: "different", file: "modules/child.py", availability: "ready", diagnostic_ids: [], graph: { nodes: [], edges: [], unresolved_connections: [], submodels: null }, input_ports: [], output_ports: [] } }
    expect(() => parsePipelineEditorDocument(badSubmodel)).toThrow(/registry key/)
  })

  it("rejects a submodel node whose label differs from its alias", () => {
    const fixture = document()
    fixture.nodes.push({
      ...fixture.nodes[0],
      recovery_id: "node:sub",
      authored_id: "sub",
      label: "Different Label",
      node_type: "submodel",
      config: { definitionId: "def_sub", alias: "sub_alias" },
    })
    expect(() => adaptPipelineEditorDocument(parsePipelineEditorDocument(fixture))).toThrow(
      "parsePipelineEditorDocument: submodel node node:sub label must equal its alias",
    )
  })
})
