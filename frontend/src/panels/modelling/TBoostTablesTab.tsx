/**
 * t-boost tables: the model itself. A t-boost prediction is the base value
 * plus one value per table on the link scale, so every number here is exactly
 * what the model scores. Under a log link the tables read as relativities.
 */
import { useState } from "react"
import type { NestedNumbers, TBoostTable, TBoostTables } from "../../api/types"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { formatChartNumber } from "../../utils/chartHelpers"
import { ChartEmptyState } from "./ChartScaffold"
import { FeatureBrowser } from "./FeatureBrowser"
import { LevelBars, StepShape, SurfaceTable } from "./termCharts"

type View = "relativity" | "score"

const MASS_LABEL = "Training mass (weight × exposure)"

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

export function TBoostTablesTab({ result }: { result: TrainResult }) {
  const report = result.tboost_tables
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [view, setView] = useState<View>("relativity")
  if (!report || (!report.tables.length && !report.factored.length)) {
    return <ChartEmptyState>No t-boost tables available</ChartEmptyState>
  }
  const relativities = report.link === "log"
  const shownView: View = relativities ? view : "score"
  const items = [
    ...report.tables.map((table) => ({ feature: table.term, importance: table.importance })),
    ...report.factored.map((effect) => ({ feature: effect.term, importance: effect.importance })),
  ]
  const activeTerm = items.some((item) => item.feature === selected) ? selected : items[0].feature
  const table = report.tables.find((candidate) => candidate.term === activeTerm)
  const base = shownView === "relativity" ? Math.exp(report.base_value) : report.base_value
  return (
    <div className="validation-feature-layout">
      <FeatureBrowser
        features={items}
        selected={activeTerm}
        onSelect={setSelected}
        search={search}
        onSearch={setSearch}
        itemNoun="table"
      />
      <div className="min-w-0">
        <div className="validation-chart-title">
          <div>
            <h4 className="validation-feature-heading">{activeTerm}</h4>
            <p className="validation-chart-description">
              {table ? (table.order === 1 ? "Main effect" : `${table.order}-way table`) : "Factored effect, no dense table"}
              {" · importance "}
              {formatChartNumber(items.find((item) => item.feature === activeTerm)?.importance ?? 0)}
              {" · base "}
              {formatChartNumber(base)}
            </p>
          </div>
          {relativities && (
            <div className="flex gap-1" role="group" aria-label="Table values">
              {([
                { key: "relativity", label: "Relativity" },
                { key: "score", label: "Link scale" },
              ] as const).map((option) => (
                <button
                  key={option.key}
                  type="button"
                  aria-pressed={shownView === option.key}
                  onClick={() => setView(option.key)}
                  className="px-2 py-0.5 rounded text-[10px] font-medium"
                  style={{
                    background: shownView === option.key ? "var(--accent-soft)" : "var(--chrome-hover)",
                    color: shownView === option.key ? "var(--accent)" : "var(--text-muted)",
                  }}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
        {table && <TableView key={table.term} table={table} view={shownView} report={report} />}
      </div>
    </div>
  )
}

function TableView({ table, view, report }: { table: TBoostTable; view: View; report: TBoostTables }) {
  const values = view === "relativity" && table.relativities ? table.relativities : table.scores
  const reference = view === "relativity" && report.link === "log" ? 1 : 0
  const valueLabel = view === "relativity" ? "Relativity" : "Score"
  const sizes = table.axes.map((axis) => axis.labels.length)
  const [rowAxis, setRowAxis] = useState(0)
  const [columnAxis, setColumnAxis] = useState(Math.min(1, table.order - 1))
  const [fixed, setFixed] = useState<number[]>(() => table.axes.map(() => 0))
  if (table.order === 1) {
    const axis = table.axes[0]
    const shape = {
      title: `Table for ${table.term}`,
      labels: axis.labels,
      values: values as number[],
      mass: table.support as number[],
      massLabel: MASS_LABEL,
      reference,
      valueLabel,
    }
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
  const sliced = table.axes.map((_, index) => index).filter((i) => i !== rowAxis && i !== columnAxis)
  return (
    <>
      {table.order > 2 && (
        <div className="mb-2 flex flex-wrap gap-3 text-xs">
          {(["row", "column"] as const).map((role) => (
            <label key={role} className="flex items-center gap-1">
              {role === "row" ? "Rows" : "Columns"}
              <select
                value={role === "row" ? rowAxis : columnAxis}
                onChange={(event) => chooseAxis(role, Number(event.target.value))}
              >
                {table.axes.map((axis, index) => (
                  <option key={axis.feature} value={index}>
                    {axis.feature}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {sliced.map((index) => (
            <label key={table.axes[index].feature} className="flex items-center gap-1">
              {table.axes[index].feature}
              <select
                value={fixed[index]}
                onChange={(event) =>
                  setFixed((current) =>
                    current.map((cell, axis) => (axis === index ? Number(event.target.value) : cell)),
                  )
                }
              >
                {table.axes[index].labels.map((label, cell) => (
                  <option key={label} value={cell}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}
      <SurfaceTable
        title={`Table for ${table.term}`}
        first={table.axes[rowAxis]}
        second={table.axes[columnAxis]}
        grid={tableSlice(values, sizes, rowAxis, columnAxis, fixed)}
        mass={tableSlice(table.support, sizes, rowAxis, columnAxis, fixed)}
        massLabel={MASS_LABEL}
        reference={reference}
        fixedOrientation
      />
    </>
  )
}
