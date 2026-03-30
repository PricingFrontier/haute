const NUM_BINS = 40
const LABEL_HEIGHT = 12

interface BandingHistogramProps {
  values: number[]
  boundaries: number[]
  height?: number
  accentColor: string
}

export function BandingHistogram({ values, boundaries, height = 50, accentColor }: BandingHistogramProps) {
  if (values.length === 0) return null

  let min = values[0]
  let max = values[0]
  for (let i = 1; i < values.length; i++) {
    if (values[i] < min) min = values[i]
    if (values[i] > max) max = values[i]
  }
  const range = max - min

  // Edge case: all values are the same — render a single centered bar
  if (range === 0) {
    const barHeight = height - LABEL_HEIGHT
    return (
      <svg role="img" aria-label="Distribution histogram" width="100%" height={height} style={{ display: "block" }}>
        <rect
          x="45%"
          y={0}
          width="10%"
          height={barHeight}
          fill="var(--text-muted)"
          opacity={0.3}
        />
        <text x="0" y={height} fontSize={9} fill="var(--text-muted)">
          {formatNum(min)}
        </text>
        {boundaries.map((_b, i) => {
          // With no range, place boundary lines at center
          return (
            <line
              key={i}
              x1="50%"
              y1={0}
              x2="50%"
              y2={barHeight}
              stroke={accentColor}
              strokeWidth={1.5}
              strokeDasharray="3,2"
            />
          )
        })}
      </svg>
    )
  }

  // Bucket values into bins
  const bins = new Array(NUM_BINS).fill(0)
  for (const v of values) {
    let idx = Math.floor(((v - min) / range) * NUM_BINS)
    if (idx >= NUM_BINS) idx = NUM_BINS - 1
    bins[idx]++
  }

  const maxBin = Math.max(...bins)
  const barAreaHeight = height - LABEL_HEIGHT
  const barWidth = 100 / NUM_BINS // percentage

  return (
    <svg role="img" aria-label="Distribution histogram" width="100%" height={height} style={{ display: "block" }} viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
      {/* Histogram bars */}
      {bins.map((count, i) => {
        if (count === 0) return null
        const barH = (count / maxBin) * barAreaHeight
        return (
          <rect
            key={i}
            x={i * barWidth}
            y={barAreaHeight - barH}
            width={barWidth}
            height={barH}
            fill="var(--text-muted)"
            opacity={0.3}
          />
        )
      })}

      {/* Boundary lines */}
      {boundaries.map((b, i) => {
        const xPct = ((b - min) / range) * 100
        if (xPct < 0 || xPct > 100) return null
        return (
          <line
            key={i}
            x1={xPct}
            y1={0}
            x2={xPct}
            y2={barAreaHeight}
            stroke={accentColor}
            strokeWidth={0.5}
            strokeDasharray="1,0.5"
          />
        )
      })}

      {/* Min/max labels */}
      <text x={0} y={height - 1} fontSize={2.5} fill="var(--text-muted)" textAnchor="start">
        {formatNum(min)}
      </text>
      <text x={100} y={height - 1} fontSize={2.5} fill="var(--text-muted)" textAnchor="end">
        {formatNum(max)}
      </text>
    </svg>
  )
}

function formatNum(n: number): string {
  if (Number.isInteger(n)) return n.toString()
  return n.toFixed(1)
}
