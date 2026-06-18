export const STRUCTURE_COLORS = {
  action: "var(--structure-action)",
  actionHover: "var(--structure-action-hover)",
  fallbackAccent: "var(--structure-action)",
  port: "var(--structure-port)",
} as const

export const STATUS_COLORS = {
  running: "var(--status-running)",
  info: "var(--status-info)",
} as const

export const MODEL_COLORS = {
  accent: "var(--model-accent)",
  accentSoft: "var(--model-accent-soft)",
  accentBorder: "var(--model-accent-border)",
  logAction: "var(--model-log-action)",
} as const

export const CHART_COLORS = {
  train: "var(--chart-train)",
  eval: "var(--chart-eval)",
  best: "var(--chart-best)",
  actual: "var(--chart-actual)",
  predicted: "var(--chart-predicted)",
  objective: "var(--chart-objective)",
  lambdaChange: "var(--chart-lambda-change)",
  residualZero: "var(--chart-residual-zero)",
  cyan: "var(--chart-cyan)",
  bandingAccent: "var(--chart-banding-accent)",
  positive: "var(--chart-positive)",
  negative: "var(--chart-negative)",
  neutral: "var(--chart-neutral)",
  optimiserSeries: [
    "var(--chart-series-1)",
    "var(--chart-series-2)",
    "var(--chart-series-3)",
    "var(--chart-series-4)",
    "var(--chart-series-5)",
    "var(--chart-series-6)",
    "var(--chart-series-7)",
    "var(--chart-series-8)",
  ],
} as const

export const NODE_GROUP_COLORS = {
  entry: "var(--node-group-entry)",
  exit: "var(--node-group-exit)",
  data: "var(--node-group-data)",
  explore: "var(--node-group-explore)",
  external: "var(--node-group-external)",
  constant: "var(--node-group-constant)",
  transform: "var(--node-group-transform)",
  model: "var(--node-group-model)",
  optimiser: "var(--node-group-optimiser)",
  structure: "var(--node-group-structure)",
  port: "var(--node-group-port)",
} as const

export const SYNTAX_COLORS = {
  keyword: "var(--syntax-keyword)",
  self: "var(--syntax-self)",
  literal: "var(--syntax-literal)",
  string: "var(--syntax-string)",
  function: "var(--syntax-function)",
  property: "var(--syntax-property)",
  operator: "var(--syntax-operator)",
  bracket: "var(--syntax-bracket)",
  meta: "var(--syntax-meta)",
} as const
