/**
 * An additive model term over one or more axes, shown as a table or a chart:
 * the t-boost Tables tab and the EBM Terms tab draw their terms with it. A
 * one-axis term is a level table, or bars or a step line; a term over two or
 * more axes is a grid of two chosen axes, the others each fixed at a chosen
 * cell, or a line per column cell across the rows.
 */
import { useState } from "react"
import type { EbmTermAxis, NestedNumbers } from "../../api/types"
import { MODEL_COLORS } from "../../theme/colors"
import { InteractionLines, LevelBars, LevelTable, StepShape, SurfaceTable } from "./termCharts"

export type TermDisplay = "table" | "chart"

export const TERM_DISPLAY_OPTIONS = [
  { key: "table", label: "Table" },
  { key: "chart", label: "Chart" },
] as const

function valueAt(tensor: NestedNumbers[], index: number[]): number {
  let node: NestedNumbers = tensor
  for (const position of index) node = (node as NestedNumbers[])[position]
  return node as number
}

/** The values of a two-axis cut through *tensor*, every other axis fixed at its selection. */
function tableSlice(
  tensor: NestedNumbers[],
  sizes: number[],
  rowAxis: number,
  columnAxis: number,
  fixed: number[],
): number[][] {
  return Array.from({ length: sizes[rowAxis] }, (_, row) =>
    Array.from({ length: sizes[columnAxis] }, (_, column) => {
      const index = [...fixed]
      index[rowAxis] = row
      index[columnAxis] = column
      return valueAt(tensor, index)
    }),
  )
}

/** A pressed-button switch between a few options, styled as the other result panes' view switches. */
export function ViewSwitch<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: readonly { key: T; label: string }[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div className="flex gap-1" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          aria-pressed={value === option.key}
          onClick={() => onChange(option.key)}
          className="rounded px-2 py-0.5 text-[12px] font-medium"
          style={{
            background: value === option.key ? MODEL_COLORS.accentSoft : "var(--chrome-hover)",
            color: value === option.key ? MODEL_COLORS.accent : "var(--text-muted)",
          }}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/**
 * Give it a ``key`` per term: the axis choices are the term's own and start
 * afresh for the next one.
 */
export function TermTableView({
  title,
  axes,
  values,
  reference,
  valueLabel,
  display,
  mass,
  massLabel,
}: {
  title: string
  axes: EbmTermAxis[]
  /** The term's values nested one array level per axis, in axis order. */
  values: NestedNumbers[]
  /** The value bars start from, the dashed line marks and cells shade away from. */
  reference: number
  valueLabel: string
  display: TermDisplay
  /** Training mass per cell, nested as ``values`` is. */
  mass?: NestedNumbers[]
  massLabel?: string
}) {
  const sizes = axes.map((axis) => axis.labels.length)
  // The longest axis runs down the rows (along a chart's x axis) and the
  // shortest other axis across the columns (a chart's lines), so a fine
  // binning scrolls down rather than sideways and a chart has few lines.
  const [rowAxis, setRowAxis] = useState(() => sizes.indexOf(Math.max(...sizes)))
  const [columnAxis, setColumnAxis] = useState(() => {
    const others = sizes.map((size, index) => (index === rowAxis ? Infinity : size))
    return others.indexOf(Math.min(...others))
  })
  const [fixed, setFixed] = useState<number[]>(() => axes.map(() => 0))
  if (axes.length === 1) {
    const axis = axes[0]
    const shape = {
      title,
      labels: axis.labels,
      values: values as number[],
      mass: mass as number[] | undefined,
      massLabel,
      reference,
      valueLabel,
    }
    if (display === "table") return <LevelTable {...shape} feature={axis.feature} />
    return axis.type === "nominal" ? <LevelBars {...shape} /> : <StepShape {...shape} />
  }
  const chooseAxis = (role: "row" | "column", axis: number) => {
    // Choosing the other role's axis swaps the two, so they always differ.
    if (role === "row") {
      if (axis === columnAxis) setColumnAxis(rowAxis)
      setRowAxis(axis)
    } else {
      if (axis === rowAxis) setRowAxis(columnAxis)
      setColumnAxis(axis)
    }
  }
  const sliced = axes.map((_, index) => index).filter((i) => i !== rowAxis && i !== columnAxis)
  const roleLabel = (role: "row" | "column") =>
    display === "table" ? (role === "row" ? "Rows" : "Columns") : role === "row" ? "Across" : "Lines"
  const grid = tableSlice(values, sizes, rowAxis, columnAxis, fixed)
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-2 text-[12px]" style={{ color: "var(--text-secondary)" }}>
        {(["row", "column"] as const).map((role) => (
          <AxisSelect
            key={role}
            label={roleLabel(role)}
            value={role === "row" ? rowAxis : columnAxis}
            onChange={(axis) => chooseAxis(role, axis)}
            options={axes.map((axis) => axis.feature)}
          />
        ))}
        {sliced.map((index) => (
          <AxisSelect
            key={axes[index].feature}
            label={axes[index].feature}
            value={fixed[index]}
            onChange={(cell) =>
              setFixed((current) => current.map((value, axis) => (axis === index ? cell : value)))
            }
            options={axes[index].labels}
          />
        ))}
      </div>
      {display === "table" ? (
        <SurfaceTable
          title={title}
          rows={axes[rowAxis]}
          columns={axes[columnAxis]}
          grid={grid}
          mass={mass && tableSlice(mass, sizes, rowAxis, columnAxis, fixed)}
          massLabel={massLabel}
          reference={reference}
        />
      ) : (
        <InteractionLines
          // A new pair of axes starts with nothing hovered: the old slot may not exist.
          key={`${rowAxis}:${columnAxis}`}
          title={title}
          across={{ ...axes[rowAxis], continuous: axes[rowAxis].type === "continuous" }}
          lines={axes[columnAxis]}
          grid={grid}
          reference={reference}
          valueLabel={valueLabel}
        />
      )}
    </>
  )
}

function AxisSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  options: string[]
}) {
  return (
    <label className="flex items-center gap-1.5">
      {label}
      <select
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="rounded border px-1 py-0.5 text-[13px]"
        style={{ background: "var(--bg-input)", borderColor: "var(--border)", color: "var(--text-primary)" }}
      >
        {options.map((option, index) => (
          <option key={option} value={index}>
            {option}
          </option>
        ))}
      </select>
    </label>
  )
}
