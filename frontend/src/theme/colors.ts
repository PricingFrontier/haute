export const STRUCTURE_COLORS = {
  action: "#64748b",
  actionHover: "#94a3b8",
  fallbackAccent: "#64748b",
  port: "#94a3b8",
} as const

export const STATUS_COLORS = {
  running: "#6366f1",
  info: "#6366f1",
} as const

export const MODEL_COLORS = {
  accent: "#a855f7",
  accentSoft: "rgba(168,85,247,.15)",
  accentBorder: "rgba(168,85,247,.3)",
  logAction: "#3b82f6",
} as const

export const CHART_COLORS = {
  train: "#a855f7",
  eval: "#22c55e",
  best: "#f59e0b",
  actual: "#22c55e",
  predicted: "#a855f7",
  objective: "#f59e0b",
  lambdaChange: "#3b82f6",
  residualZero: "#ef4444",
  cyan: "#06b6d4",
  bandingAccent: "#22d3ee",
  positive: "#4caf50",
  negative: "#f44336",
  neutral: "#9e9e9e",
  optimiserSeries: [
    "#f59e0b",
    "#3b82f6",
    "#22c55e",
    "#ef4444",
    "#a855f7",
    "#06b6d4",
    "#f97316",
    "#ec4899",
  ],
} as const

export const NODE_GROUP_COLORS = {
  entry: "#E69F00",
  exit: "#D55E00",
  data: "#00B386",
  external: "#B07AA1",
  constant: "#94a3b8",
  transform: "#56B4E9",
  model: "#CC79A7",
  optimiser: "#D4B82A",
  structure: "#7B8DA0",
  port: "#94a3b8",
} as const

export const SYNTAX_COLORS = {
  keyword: "#c084fc",
  self: "#f472b6",
  literal: "#fb923c",
  string: "#86efac",
  function: "#93c5fd",
  property: "#67e8f9",
  operator: "#94a3b8",
  bracket: "#cbd5e1",
  meta: "#a78bfa",
} as const
