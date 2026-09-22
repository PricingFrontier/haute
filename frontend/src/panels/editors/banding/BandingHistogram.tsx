import type { BandingHistogramBin } from "../../../api/types"

const LABEL_HEIGHT = 12

interface BandingHistogramProps {
  /**
   * The distribution to draw, as the server sends it or as the preview
   * fallback builds it — one shape, so the picture does not depend on where
   * the numbers came from.
   */
  bins: BandingHistogramBin[]
  boundaries: number[]
  height?: number
  accentColor: string
}

export function BandingHistogram({
  bins,
  boundaries,
  height = 50,
  accentColor,
}: BandingHistogramProps) {
  if (bins.length === 0) return null

  const min = bins[0].lower
  const max = bins[bins.length - 1].upper
  const range = max - min
  const maxCount = Math.max(...bins.map((bin) => bin.count))
  const barAreaHeight = height - LABEL_HEIGHT

  // A constant column is one bin with no width: draw it centred rather than
  // dividing by a zero range.
  if (range === 0) {
    return (
      <svg
        role="img"
        aria-label="Distribution histogram"
        width="100%"
        height={height}
        style={{ display: "block" }}
      >
        <rect x="45%" y={0} width="10%" height={barAreaHeight} fill="var(--text-muted)" opacity={0.3} />
        <text x="0" y={height} fontSize={9} fill="var(--text-muted)">
          {formatNum(min)}
        </text>
        {boundaries.map((_boundary, index) => (
          <line
            key={index}
            x1="50%"
            y1={0}
            x2="50%"
            y2={barAreaHeight}
            stroke={accentColor}
            strokeWidth={1.5}
            strokeDasharray="3,2"
          />
        ))}
      </svg>
    )
  }

  return (
    <svg
      role="img"
      aria-label="Distribution histogram"
      width="100%"
      height={height}
      style={{ display: "block" }}
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
    >
      {bins.map((bin, index) => {
        if (bin.count === 0 || maxCount === 0) return null
        const barHeight = (bin.count / maxCount) * barAreaHeight
        const x = ((bin.lower - min) / range) * 100
        const width = Math.max(((bin.upper - bin.lower) / range) * 100, 0.5)
        return (
          <rect
            key={index}
            x={x}
            y={barAreaHeight - barHeight}
            width={width}
            height={barHeight}
            fill="var(--text-muted)"
            opacity={0.3}
          />
        )
      })}

      {boundaries.map((boundary, index) => {
        const xPct = ((boundary - min) / range) * 100
        if (xPct < 0 || xPct > 100) return null
        return (
          <line
            key={index}
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
