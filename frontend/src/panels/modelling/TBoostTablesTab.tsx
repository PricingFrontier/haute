/**
 * t-boost tables: the model itself. A t-boost prediction is the base value
 * plus one value per table on the link scale, so every number here is exactly
 * what the model scores. Under a log link the tables read as relativities.
 * Each table shows as a table by default, with a chart of it a click away.
 */
import { useState } from "react"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { formatChartNumber } from "../../utils/chartHelpers"
import { ChartEmptyState } from "./ChartScaffold"
import { FeatureBrowser } from "./FeatureBrowser"
import { TERM_DISPLAY_OPTIONS, TermTableView, ViewSwitch, type TermDisplay } from "./TermTableView"

type Scale = "relativity" | "score"

const MASS_LABEL = "Training mass (weight × exposure)"

export function TBoostTablesTab({ result }: { result: TrainResult }) {
  const report = result.tboost_tables
  const [selected, setSelected] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  const [scale, setScale] = useState<Scale>("relativity")
  const [display, setDisplay] = useState<TermDisplay>("table")
  if (!report || (!report.tables.length && !report.factored.length)) {
    return <ChartEmptyState>No t-boost tables available</ChartEmptyState>
  }
  const relativities = report.link === "log"
  const shownScale: Scale = relativities ? scale : "score"
  const items = [
    ...report.tables.map((table) => ({ feature: table.term, importance: table.importance })),
    ...report.factored.map((effect) => ({ feature: effect.term, importance: effect.importance })),
  ]
  const activeTerm = items.some((item) => item.feature === selected) ? selected : items[0].feature
  const table = report.tables.find((candidate) => candidate.term === activeTerm)
  const base = shownScale === "relativity" ? Math.exp(report.base_value) : report.base_value
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
          {table && (
            <div className="flex flex-wrap gap-3">
              <ViewSwitch
                label="Table display"
                options={TERM_DISPLAY_OPTIONS}
                value={display}
                onChange={setDisplay}
              />
              {relativities && (
                <ViewSwitch
                  label="Table values"
                  options={[
                    { key: "relativity", label: "Relativity" },
                    { key: "score", label: "Link scale" },
                  ]}
                  value={shownScale}
                  onChange={setScale}
                />
              )}
            </div>
          )}
        </div>
        {table && (
          <TermTableView
            key={table.term}
            title={`Table for ${table.term}`}
            axes={table.axes}
            values={shownScale === "relativity" && table.relativities ? table.relativities : table.scores}
            reference={shownScale === "relativity" ? 1 : 0}
            valueLabel={shownScale === "relativity" ? "Relativity" : "Score"}
            display={display}
            mass={table.support}
            massLabel={MASS_LABEL}
          />
        )}
      </div>
    </div>
  )
}
