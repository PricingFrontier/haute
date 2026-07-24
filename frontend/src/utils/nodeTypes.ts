import { Database, Brain, TableProperties, CircleDot, HardDriveDownload, FileArchive, Package, ArrowRight, Radio, ToggleLeft, SlidersHorizontal, FlaskConical, Target, Crosshair, Rows3, Hash, Search, GitMerge } from "lucide-react"
import PolarsIcon from "../components/PolarsIcon"
import { NODE_GROUP_COLORS } from "../theme/colors"

export const NODE_TYPES = {
  API_INPUT: "apiInput",
  DATA_INPUT: "dataInput",
  DATA_OUTPUT: "dataOutput",
  POLARS: "polars",
  EDGE_JOIN: "edgeJoin",
  MODEL_SCORE: "modelScore",
  BANDING: "banding",
  RATING_STEP: "ratingStep",
  OUTPUT: "output",
  EXPLORE: "explore",
  EXTERNAL_FILE: "externalFile",
  LIVE_SWITCH: "liveSwitch",
  MODELLING: "modelling",
  OPTIMISER: "optimiser",
  OPTIMISER_APPLY: "optimiserApply",
  SCENARIO_EXPANDER: "scenarioExpander",
  CONSTANT: "constant",
  SUBMODEL: "submodel",
  SUBMODEL_PORT: "submodelPort",
} as const

export type NodeTypeValue = typeof NODE_TYPES[keyof typeof NODE_TYPES]

/**
 * Single source of truth for all node type metadata.
 *
 * - icon:          Lucide icon component (canvas node + palette)
 * - color:         Hex accent color (canvas node + palette + editors)
 * - label:         Short UPPER CASE badge text shown on canvas nodes
 * - name:          Full Title Case display name (palette, tooltips, dialogs)
 * - description:   One-line tooltip for the palette and help UI
 * - defaultConfig: Initial config object when a node of this type is created
 */
export const NODE_TYPE_META: Record<NodeTypeValue, {
  icon: React.ElementType
  color: string
  label: string
  name: string
  description: string
  defaultConfig: Record<string, unknown>
  maxInputs?: number
  /** Shape variant for color-blind differentiation: "pill" = more rounded (entry/exit nodes). */
  shape?: "pill"
  size?: "compact"
  /** React Flow origin for nodes whose stored position should represent their centre. */
  origin?: [number, number]
}> = {
  // Entry group (orange) — pipeline starts here
  // Palette: Okabe-Ito / Wong CVD-safe — each functional group gets a distinct hue
  [NODE_TYPES.API_INPUT]:          { icon: Radio,              color: NODE_GROUP_COLORS.entry, label: "QUOTE IN",       name: "Quote Input",          description: "Live API input for deployment (max 1)",                       defaultConfig: { path: "" }, shape: "pill" },
  [NODE_TYPES.LIVE_SWITCH]:        { icon: ToggleLeft,         color: NODE_GROUP_COLORS.entry, label: "SWITCH",         name: "Source Switch",        description: "Switch between live API and batch data",                      defaultConfig: { mode: "live" }, shape: "pill" },
  // Exit (vermillion) — pipeline destination
  [NODE_TYPES.OUTPUT]:             { icon: CircleDot,          color: NODE_GROUP_COLORS.exit, label: "QUOTE OUT",      name: "Quote Response",       description: "Final price / prediction",                                    defaultConfig: { fields: [] }, shape: "pill" },
  // Data group (bluish green) — read/write external data
  [NODE_TYPES.DATA_INPUT]:         { icon: Database, color: NODE_GROUP_COLORS.data, label: "DATA IN", name: "Data Input", description: "Read a configured external dataset", defaultConfig: { inputType: "file", cacheMode: "direct", format: "parquet", mode: "scan", path: "", arguments: {}, code: "" } },
  [NODE_TYPES.DATA_OUTPUT]:        { icon: HardDriveDownload, color: NODE_GROUP_COLORS.data, label: "DATA OUT", name: "Data Output", description: "Write a configured external dataset", defaultConfig: { outputType: "file", format: "parquet", mode: "sink", path: "", arguments: {} }, maxInputs: 1 },
  [NODE_TYPES.EXPLORE]:            { icon: Search,             color: NODE_GROUP_COLORS.explore, label: "EXPLORE",        name: "Explore",              description: "Automatic analysis of an upstream dataset",                   defaultConfig: {}, maxInputs: 1 },
  [NODE_TYPES.EXTERNAL_FILE]:      { icon: FileArchive,        color: NODE_GROUP_COLORS.external, label: "LOAD FILE",      name: "Load File",            description: "Load a pickle, JSON, or joblib file and use in code",         defaultConfig: { path: "", fileType: "pickle", code: "" } },
  [NODE_TYPES.CONSTANT]:           { icon: Hash,               color: NODE_GROUP_COLORS.constant, label: "CONSTANT",       name: "Constant",             description: "Named constant values (1-row DataFrame)",                     defaultConfig: { values: [{ name: "constant_1", value: "1.0" }] } },
  // Transform group (sky blue) — process/reshape data
  [NODE_TYPES.POLARS]:             { icon: PolarsIcon,         color: NODE_GROUP_COLORS.transform, label: "POLARS",         name: "Polars",               description: "Polars transform / feature engineering",                      defaultConfig: {} },
  [NODE_TYPES.EDGE_JOIN]:          { icon: GitMerge,           color: NODE_GROUP_COLORS.transform, label: "JOIN",           name: "Edge Join",            description: "Join two incoming dataframes by dropping a connection on an edge", defaultConfig: { how: "left", suffix: "_right" }, maxInputs: 2, size: "compact", origin: [0.5, 0.5] },
  [NODE_TYPES.BANDING]:            { icon: SlidersHorizontal,  color: NODE_GROUP_COLORS.transform, label: "BANDING",        name: "Banding",              description: "Group numerical or categorical values into bands",             defaultConfig: { factors: [{ banding: "continuous", column: "", outputColumn: "", rules: [], default: null }] }, maxInputs: 1 },
  [NODE_TYPES.SCENARIO_EXPANDER]:  { icon: Rows3,              color: NODE_GROUP_COLORS.transform, label: "EXPANDER",       name: "Expander",             description: "Cross-join rows with scenario values (price, tier, etc.)",    defaultConfig: {}, maxInputs: 1 },
  [NODE_TYPES.RATING_STEP]:        { icon: TableProperties,    color: NODE_GROUP_COLORS.transform, label: "RATING",         name: "Rating Step",          description: "Lookup, factor, cap/floor",                                   defaultConfig: { tables: [{ name: "Table 1", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }], operation: "multiply", combinedColumn: "", combinedOutputs: [], code: "" }, maxInputs: 1 },
  // Model group (reddish purple) — ML training & scoring
  [NODE_TYPES.MODELLING]:          { icon: FlaskConical,       color: NODE_GROUP_COLORS.model, label: "TRAINING",       name: "Model Training",       description: "Train a CatBoost or GLM model",                         defaultConfig: {}, maxInputs: 1 },
  [NODE_TYPES.MODEL_SCORE]:        { icon: Brain,              color: NODE_GROUP_COLORS.model, label: "SCORING",        name: "Model Scoring",        description: "Score using an MLflow model",                                 defaultConfig: { sourceType: "registered", registered_model: "", version: "latest", task: "regression", output_column: "prediction", code: "" }, maxInputs: 1 },
  // Optimisation group (gold) — price optimisation
  [NODE_TYPES.OPTIMISER]:          { icon: Target,             color: NODE_GROUP_COLORS.optimiser, label: "OPTIMISATION",   name: "Optimisation",         description: "Price optimisation via Lagrangian solver",                     defaultConfig: { mode: "online", objective: "", constraints: {}, quote_id: "quote_id", scenario_index: "scenario_index", scenario_value: "scenario_value", max_iter: 50, tolerance: 1e-6 } },
  [NODE_TYPES.OPTIMISER_APPLY]:    { icon: Crosshair,          color: NODE_GROUP_COLORS.optimiser, label: "APPLY OPT",     name: "Apply Optimisation",   description: "Apply saved optimisation results (lambdas or factor tables)",  defaultConfig: { sourceType: "file", artifact_path: "", version_column: "__optimiser_version__", optimised_value_column: "optimised_value" } },
  // Structure (slate) — composition & utility
  [NODE_TYPES.SUBMODEL]:           { icon: Package,            color: NODE_GROUP_COLORS.structure, label: "SUBMODEL",       name: "Submodel",             description: "Reusable sub-pipeline",                                       defaultConfig: {} },
  [NODE_TYPES.SUBMODEL_PORT]:      { icon: ArrowRight,         color: NODE_GROUP_COLORS.port, label: "PORT",           name: "Port",                 description: "Submodel input/output port",                                  defaultConfig: {} },
}

export const SINGLETON_TYPES = new Set<NodeTypeValue>([
  NODE_TYPES.API_INPUT, NODE_TYPES.OUTPUT,
])

/** Whether a node type allows only one instance per pipeline. */
export function isSingletonType(nodeType: string | undefined): boolean {
  return Boolean(nodeType && SINGLETON_TYPES.has(nodeType as NodeTypeValue))
}

/** Nodes that only produce data — no input handle. */
export const SOURCE_ONLY_TYPES = new Set<string>([
  NODE_TYPES.DATA_INPUT, NODE_TYPES.API_INPUT, NODE_TYPES.CONSTANT,
])

/** Nodes whose newly-added columns originate from generated scenario/config data. */
export const GENERATED_COLUMN_ORIGIN_TYPES = new Set<string>([
  NODE_TYPES.SCENARIO_EXPANDER,
])

/** Nodes that only consume data — no output handle. */
export const SINK_ONLY_TYPES = new Set<string>([
  NODE_TYPES.OUTPUT, NODE_TYPES.DATA_OUTPUT, NODE_TYPES.EXPLORE, NODE_TYPES.MODELLING, NODE_TYPES.OPTIMISER,
])

/** Node types shown in the palette, in display order. Submodel/port are excluded (created via dialog). */
export const PALETTE_TYPES: NodeTypeValue[] = [
  NODE_TYPES.API_INPUT, NODE_TYPES.LIVE_SWITCH, NODE_TYPES.OUTPUT,
  NODE_TYPES.DATA_INPUT, NODE_TYPES.DATA_OUTPUT, NODE_TYPES.EXTERNAL_FILE, NODE_TYPES.CONSTANT,
  NODE_TYPES.POLARS, NODE_TYPES.SCENARIO_EXPANDER, NODE_TYPES.BANDING, NODE_TYPES.RATING_STEP, NODE_TYPES.EXPLORE,
  NODE_TYPES.MODELLING, NODE_TYPES.MODEL_SCORE,
  NODE_TYPES.OPTIMISER, NODE_TYPES.OPTIMISER_APPLY,
]

/** Derived lookups used by components that need one metadata field. */
export const nodeTypeIcons: Record<string, React.ElementType> =
  Object.fromEntries(Object.entries(NODE_TYPE_META).map(([k, v]) => [k, v.icon]))

export const nodeTypeColors: Record<string, string> =
  Object.fromEntries(Object.entries(NODE_TYPE_META).map(([k, v]) => [k, v.color]))

export const nodeTypeLabels: Record<string, string> =
  Object.fromEntries(Object.entries(NODE_TYPE_META).map(([k, v]) => [k, v.label]))

/** Node types that use pill shape (more rounded) — visually distinct for entry/exit nodes. */
export const PILL_TYPES = new Set<string>(
  Object.entries(NODE_TYPE_META).filter(([, v]) => v.shape === "pill").map(([k]) => k)
)
