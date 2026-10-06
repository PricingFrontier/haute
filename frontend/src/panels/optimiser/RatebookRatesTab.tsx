/** The ratebook Rates tab: each factor's chosen rates in the per-feature diagnostic layout. */
import { useMemo, useState } from "react"
import { ChartLegend, ChartValuesTable } from "../modelling/ChartScaffold"
import type { FeatureRanking } from "../modelling/FeatureBrowser"
import { FeatureDiagnosticLayout } from "../modelling/FeatureDiagnosticTab"
import { useDiagnosticFeature, type SharedFeatureBrowser } from "../modelling/useDiagnosticFeature"
import {
  RELATIVITY_ABOVE_COLOR,
  RELATIVITY_BELOW_COLOR,
  RelativityBars,
  type RelativityBar,
} from "../RelativityBars"
import {
  factorRateSpread,
  formatRate,
  formatVsNeutral,
  levelQuoteShares,
  orderedFactorTableEntries,
  type FactorLevelOrder,
  type FactorTableRow,
  type FactorTables,
} from "./ratebookFactorTables"

/** The factor choice and search, owned by the result workspace so they outlive the tab. */
export type RatesFactorSelection = Omit<SharedFeatureBrowser, "features">

interface RatebookRatesTabProps {
  factorTables: FactorTables
  factorLevelOrder?: FactorLevelOrder
  selection?: RatesFactorSelection
}

const RATE_SPREAD: FeatureRanking = {
  label: "Rate spread",
  description: "How far the factor's rates move from 1.0, weighted by quotes.",
}

/** From this many levels the bars compact and level labels thin. */
const COMPACT_FROM_LEVELS = 25
const QUOTE_STRIP_WIDTH = 96

type RatesFactor = { feature: string; rows: FactorTableRow[] }

export default function RatebookRatesTab({ factorTables, factorLevelOrder, selection }: RatebookRatesTabProps) {
  const factors = useMemo<RatesFactor[]>(
    () => orderedFactorTableEntries(factorTables, factorLevelOrder).map(([feature, rows]) => ({ feature, rows })),
    [factorTables, factorLevelOrder],
  )
  const ranked = useMemo(
    () =>
      factors
        .map(({ feature, rows }) => ({ feature, importance: factorRateSpread(feature, rows) }))
        .sort((a, b) => b.importance - a.importance || a.feature.localeCompare(b.feature)),
    [factors],
  )
  const browser = useDiagnosticFeature(
    ranked,
    selection && {
      ...selection,
      features: ranked,
      selected: ranked.some((item) => item.feature === selection.selected)
        ? selection.selected
        : (ranked[0]?.feature ?? null),
    },
  )
  return (
    <FeatureDiagnosticLayout
      rows={factors}
      browser={browser}
      rankedBy={RATE_SPREAD}
      itemNoun="factor"
      noun="rate"
      renderChart={(factor) => <RatesFactorChart key={factor.feature} factor={factor} />}
    />
  )
}

function RatesFactorChart({ factor }: { factor: RatesFactor }) {
  const [activeLevel, setActiveLevel] = useState<string | null>(null)
  const { feature, rows } = factor
  const shares = levelQuoteShares(feature, rows)
  const totalQuotes = rows.reduce((total, row) => total + row.quote_count, 0)
  const maxQuotes = Math.max(...rows.map((row) => row.quote_count))
  const rates = rows.map((row) => row.optimal_scenario_value)
  const byLevel = new Map(rows.map((row, index) => [row.__factor_group__, { row, share: shares[index] }]))
  const active = activeLevel === null ? null : byLevel.get(activeLevel)!
  const bars: RelativityBar[] = rows.map((row) => ({
    key: row.__factor_group__,
    label: row.__factor_group__,
    value: row.optimal_scenario_value,
  }))

  return (
    <>
      <div className="validation-chart-title">
        <div>
          <h4 className="validation-feature-heading">{feature}</h4>
          <p className="validation-chart-description">
            {`${rows.length.toLocaleString()} levels · ${totalQuotes.toLocaleString()} quotes · rates ${formatRate(Math.min(...rates))} to ${formatRate(Math.max(...rates))}`}
          </p>
        </div>
        <ChartLegend
          items={[
            { label: "Above 1.0 (price up)", color: RELATIVITY_ABOVE_COLOR, swatch: "bar" },
            { label: "Below 1.0 (price down)", color: RELATIVITY_BELOW_COLOR, swatch: "bar" },
            { label: "Quotes", color: "var(--text-muted)", swatch: "bar", opacity: 0.5 },
          ]}
        />
      </div>
      <RelativityBars
        ariaLabel={`Rates for ${feature}`}
        bars={bars}
        formatValue={formatRate}
        compactFrom={COMPACT_FROM_LEVELS}
        interaction={{
          activeKey: activeLevel,
          onActivate: setActiveLevel,
          describe: (bar) => {
            const { row, share } = byLevel.get(bar.key)!
            return (
              `${bar.label}. Rate ${formatRate(row.optimal_scenario_value)}, `
              + `${formatVsNeutral(row.optimal_scenario_value)} vs neutral 1.0. `
              + `${row.quote_count.toLocaleString()} quotes, ${share.toFixed(1)}% of the factor's quotes.`
            )
          },
        }}
        aside={{
          header: `Quotes 0–${maxQuotes.toLocaleString()}`,
          width: QUOTE_STRIP_WIDTH,
          render: (bar, isActive) => {
            const { row } = byLevel.get(bar.key)!
            return (
              <span className="block h-2 w-full">
                <span
                  data-testid="quote-strip-bar"
                  className="block h-full rounded-sm"
                  title={`${bar.label}: ${row.quote_count.toLocaleString()} quotes`}
                  style={{
                    // levelQuoteShares has thrown already if the factor has no quotes.
                    width: `${(row.quote_count / maxQuotes) * 100}%`,
                    background: "var(--text-muted)",
                    opacity: isActive ? 0.65 : 0.3,
                  }}
                />
              </span>
            )
          },
        }}
      />
      <div className="validation-bin-detail" role="status" aria-live="polite">
        {active ? (
          <>
            <strong>{active.row.__factor_group__}</strong>
            <span>Rate: {formatRate(active.row.optimal_scenario_value)}</span>
            <span>vs neutral 1.0: {formatVsNeutral(active.row.optimal_scenario_value)}</span>
            <span>Quotes: {active.row.quote_count.toLocaleString()}</span>
            <span>Share: {active.share.toFixed(1)}%</span>
          </>
        ) : (
          <span>Hover or focus a level to inspect its rate.</span>
        )}
      </div>
      <ChartValuesTable
        summary="View rate values"
        ariaLabel={`${feature} rate values`}
        headers={["Level", "Rate", "vs neutral 1.0 (%)", "Quotes", "Share"]}
        rows={rows.map((row, index) => [
          row.__factor_group__,
          formatRate(row.optimal_scenario_value),
          formatVsNeutral(row.optimal_scenario_value),
          row.quote_count.toLocaleString(),
          `${shares[index].toFixed(1)}%`,
        ])}
      />
    </>
  )
}
