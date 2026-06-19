/**
 * ReactFlow node type → component registry.
 *
 * The single source of truth for which React component renders each node type.
 * Shared by the main canvas (App) AND the read-only wrapper Peek
 * (SubmodelPeekBody), so the peek renders the EXACT same node cards as the
 * canvas — a faithful window into the wrapper's internals rather than a bespoke
 * schematic.
 */
import type { NodeTypes } from "@xyflow/react"
import PipelineNode from "./PipelineNode"
import SubmodelNode from "./SubmodelNode"
import SubmodelPortNode from "./SubmodelPortNode"
import { NODE_TYPES } from "../utils/nodeTypes"

export const nodeTypes: NodeTypes = {
  [NODE_TYPES.API_INPUT]: PipelineNode,
  [NODE_TYPES.DATA_SOURCE]: PipelineNode,
  [NODE_TYPES.POLARS]: PipelineNode,
  [NODE_TYPES.EDGE_JOIN]: PipelineNode,
  [NODE_TYPES.MODEL_SCORE]: PipelineNode,
  [NODE_TYPES.RATING_STEP]: PipelineNode,
  [NODE_TYPES.BANDING]: PipelineNode,
  [NODE_TYPES.OUTPUT]: PipelineNode,
  [NODE_TYPES.DATA_SINK]: PipelineNode,
  [NODE_TYPES.EXPLORE]: PipelineNode,
  [NODE_TYPES.EXTERNAL_FILE]: PipelineNode,
  [NODE_TYPES.LIVE_SWITCH]: PipelineNode,
  [NODE_TYPES.MODELLING]: PipelineNode,
  [NODE_TYPES.OPTIMISER]: PipelineNode,
  [NODE_TYPES.OPTIMISER_APPLY]: PipelineNode,
  [NODE_TYPES.SCENARIO_EXPANDER]: PipelineNode,
  [NODE_TYPES.CONSTANT]: PipelineNode,
  [NODE_TYPES.SUBMODEL]: SubmodelNode,
  [NODE_TYPES.SUBMODEL_PORT]: SubmodelPortNode,
}
