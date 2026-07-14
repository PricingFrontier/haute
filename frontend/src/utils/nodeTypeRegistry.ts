/**
 * ReactFlow node-type → component registry.
 *
 * Single source of truth shared by the live editor canvas (App) and the
 * read-only comparison canvases (ComparisonView), so the two never drift on
 * which component renders a given node type.
 */
import PipelineNode from "../nodes/PipelineNode"
import SubmodelNode from "../nodes/SubmodelNode"
import SubmodelPortNode from "../nodes/SubmodelPortNode"
import { NODE_TYPES } from "./nodeTypes"

export const nodeTypes = {
  [NODE_TYPES.API_INPUT]: PipelineNode,
  [NODE_TYPES.DATA_SOURCE]: PipelineNode,
  [NODE_TYPES.DATA_INPUT]: PipelineNode,
  [NODE_TYPES.DATA_OUTPUT]: PipelineNode,
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
