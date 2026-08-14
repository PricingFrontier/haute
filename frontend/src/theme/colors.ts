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

export const PIVOT_CHART_COLORS = {
  defaultSeries: [
    "#D97706",
    "#2563EB",
    "#059669",
    "#DC2626",
    "#9333EA",
    "#0891B2",
    "#EA580C",
    "#DB2777",
  ],
  fallback: {
    background: "#111827",
    text: "#F9FAFB",
    muted: "#9CA3AF",
    grid: "#374151",
    series: [
      "#F59E0B",
      "#3B82F6",
      "#10B981",
      "#EF4444",
      "#A855F7",
      "#06B6D4",
      "#F97316",
      "#EC4899",
    ],
  },
} as const

export const PIVOT_CONDITIONAL_FORMAT_COLORS = {
  low: { hex: "#fecaca", rgb: [254, 202, 202] },
  midpoint: { hex: "#fef08a", rgb: [254, 240, 138] },
  high: { hex: "#bbf7d0", rgb: [187, 247, 208] },
  cellText: "#111827",
} as const

export const NODE_GROUP_COLORS = {
  entry: "#E69F00",
  exit: "#D55E00",
  data: "#00B386",
  explore: "#BE185D",
  external: "#B07AA1",
  constant: "#94a3b8",
  transform: "#56B4E9",
  model: "#CC79A7",
  optimiser: "#D4B82A",
  structure: "#7B8DA0",
  port: "#94a3b8",
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
