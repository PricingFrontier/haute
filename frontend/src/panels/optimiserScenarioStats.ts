import { ScenarioDataError, type ScenarioRow } from "./optimiser/scenarioRows"

export type ScenarioSummary = {
  mean: number
  std: number
  min: number
  p25: number
  median: number
  p75: number
  max: number
}

export type ScenarioStats = {
  scenarioIndex: number
  scenarioValue: number
  count: number
  /** Null when the series has no value at this scenario. */
  summary: ScenarioSummary | null
}

type ScenarioStatsInput = {
  /** Rows read by `readScenarioRows`: every value present is finite. */
  rows: Iterable<ScenarioRow>
  series: string[]
  /** The scenario value column, named when rows disagree on a scenario's value. */
  scenarioValueCol: string
}

type ScenarioBucket = {
  scenarioValue: number
  valuesBySeries: Map<string, number[]>
}

function summaryOf(values: number[]): ScenarioSummary | null {
  const vals = [...values].sort((a, b) => a - b)
  const n = vals.length
  if (n === 0) return null

  const sum = vals.reduce((a, b) => a + b, 0)
  const mean = sum / n
  const variance = vals.reduce((a, v) => a + (v - mean) ** 2, 0) / n
  const percentile = (p: number) => {
    const idx = (p / 100) * (n - 1)
    const lo = Math.floor(idx)
    const hi = Math.ceil(idx)
    if (lo === hi) return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)
  }

  return {
    mean,
    std: Math.sqrt(variance),
    min: vals[0],
    p25: percentile(25),
    median: percentile(50),
    p75: percentile(75),
    max: vals[n - 1],
  }
}

/** Per series, the statistics of each scenario in the rows, in scenario order. */
export function computeScenarioStatsBySeries({
  rows,
  series,
  scenarioValueCol,
}: ScenarioStatsInput): Map<string, ScenarioStats[]> {
  const bucketsByScenario = new Map<number, ScenarioBucket>()

  for (const { scenarioIndex, scenarioValue, values: rowValues } of rows) {
    let bucket = bucketsByScenario.get(scenarioIndex)
    if (!bucket) {
      bucket = { scenarioValue, valuesBySeries: new Map() }
      bucketsByScenario.set(scenarioIndex, bucket)
    } else if (bucket.scenarioValue !== scenarioValue) {
      throw new ScenarioDataError(
        `Conflicting ${scenarioValueCol} for scenario ${scenarioIndex}: ${bucket.scenarioValue} vs ${scenarioValue}`,
      )
    }

    for (const column of series) {
      // A series without a value in this row is left out of its statistics.
      if (!(column in rowValues)) continue
      const values = bucket.valuesBySeries.get(column)
      if (values) values.push(rowValues[column])
      else bucket.valuesBySeries.set(column, [rowValues[column]])
    }
  }

  const scenarios = [...bucketsByScenario.entries()].sort(([a], [b]) => a - b)
  return new Map(
    series.map((column) => [
      column,
      scenarios.map(([scenarioIndex, bucket]) => {
        const values = bucket.valuesBySeries.get(column) ?? []
        return { scenarioIndex, scenarioValue: bucket.scenarioValue, count: values.length, summary: summaryOf(values) }
      }),
    ]),
  )
}
