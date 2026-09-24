import type { BandingHistogramBin } from "../../../api/types"
import { formatChartNumber } from "../../../utils/chartHelpers"
import { ChartSvg, ResponsiveChart } from "../../modelling/ChartScaffold"

const LABEL_HEIGHT = 12
const LABEL_FONT_SIZE = 9

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
  /** A fixed pixel width; by default the chart follows its container. */
  width?: number
}

export function BandingHistogram({
  bins,
  boundaries,
  height = 50,
  accentColor,
  width,
}: BandingHistogramProps) {
  if (bins.length === 0) return null
  return (
    <ResponsiveChart width={width}>
      {(chartWidth) => (
        <HistogramSvg
          bins={bins}
          boundaries={boundaries}
          height={height}
          width={chartWidth}
          accentColor={accentColor}
        />
      )}
    </ResponsiveChart>
  )
}

function HistogramSvg({
  bins,
  boundaries,
  height,
  width,
  accentColor,
}: Required<Omit<BandingHistogramProps, "width">> & { width: number }) {
  // The bars run edge to edge over exactly the binned data, with no padding:
  // the band boundaries are read against the data's own ends.
  const min = bins[0].lower
  const max = bins[bins.length - 1].upper
  const range = max - min
  const maxCount = Math.max(...bins.map((bin) => bin.count))
  const barAreaHeight = height - LABEL_HEIGHT
  // A constant column is one bin with no width: it sits in the middle.
  const x = (value: number) => (range === 0 ? width / 2 : ((value - min) / range) * width)

  return (
    <ChartSvg ariaLabel="Distribution histogram" width={width} height={height}>
      {range === 0 ? (
        <rect
          x={width * 0.45}
          y={0}
          width={width * 0.1}
          height={barAreaHeight}
          fill="var(--text-muted)"
          opacity={0.3}
        />
      ) : (
        bins.map((bin, index) => {
          if (bin.count === 0 || maxCount === 0) return null
          const barHeight = (bin.count / maxCount) * barAreaHeight
          return (
            <rect
              key={index}
              x={x(bin.lower)}
              y={barAreaHeight - barHeight}
              width={Math.max(x(bin.upper) - x(bin.lower), width * 0.005)}
              height={barHeight}
              fill="var(--text-muted)"
              opacity={0.3}
            />
          )
        })
      )}

      {boundaries.map((boundary, index) => {
        if (range !== 0 && (boundary < min || boundary > max)) return null
        const position = x(boundary)
        return (
          <line
            key={index}
            x1={position}
            y1={0}
            x2={position}
            y2={barAreaHeight}
            stroke={accentColor}
            strokeWidth={1.5}
            strokeDasharray="3,2"
          />
        )
      })}

      <text x={0} y={height - 1} fontSize={LABEL_FONT_SIZE} fill="var(--text-muted)" textAnchor="start">
        {formatChartNumber(min)}
      </text>
      {range !== 0 && (
        <text x={width} y={height - 1} fontSize={LABEL_FONT_SIZE} fill="var(--text-muted)" textAnchor="end">
          {formatChartNumber(max)}
        </text>
      )}
    </ChartSvg>
  )
}
