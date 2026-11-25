import { ScenarioDataError, type ScenarioRow } from "./optimiser/scenarioRows"

export type ScenarioStats = {
  scenarioIndex: number
  scenarioValue: number
  count: number
  mean: number
  std: number
  min: number
  p25: number
  median: number
  p75: number
  max: number
}

type ScenarioStatsInput = {
  /** Rows read by `readScenarioRows`: every value present is finite. */
  rows: Iterable<ScenarioRow>
  scenarioIndices?: number[]
  series: string[]
  /** The scenario value column, named when rows disagree on a scenario's value. */
  scenarioValueCol: string
}

type ScenarioBucket = {
  scenarioValue: number
  valuesBySeries: Map<string, number[]>
}

function emptyScenarioStats(scenarioIndex: number, scenarioValue = 0): ScenarioStats {
  return { scenarioIndex, scenarioValue, count: 0, mean: 0, std: 0, min: 0, p25: 0, median: 0, p75: 0, max: 0 }
}

function statsFromValues(scenarioIndex: number, scenarioValue: number, values: number[]): ScenarioStats {
  const vals = [...values].sort((a, b) => a - b)
  const n = vals.length
  if (n === 0) return emptyScenarioStats(scenarioIndex, scenarioValue)

  const sum = vals.reduce((a, b) => a + b, 0)
  const mean = sum / n
  const variance = vals.reduce((a, v) => a + (v - mean) ** 2, 0) / n
  const std = Math.sqrt(variance)
  const percentile = (p: number) => {
    const idx = (p / 100) * (n - 1)
    const lo = Math.floor(idx)
    const hi = Math.ceil(idx)
    if (lo === hi) return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (idx - lo)
  }

  return {
    scenarioIndex,
    scenarioValue,
    count: n,
    mean,
    std,
    min: vals[0],
    p25: percentile(25),
    median: percentile(50),
    p75: percentile(75),
    max: vals[n - 1],
  }
}

export function computeScenarioStatsBySeries({
  rows,
  scenarioIndices,
  series,
  scenarioValueCol,
}: ScenarioStatsInput): Map<string, ScenarioStats[]> {
  const bucketsByScenario = new Map<number, ScenarioBucket>()
  const discoveredScenarioIndices: number[] = []
  const shouldDiscoverScenarioIndices = scenarioIndices == null

  for (const { scenarioIndex, scenarioValue, values: rowValues } of rows) {
    let bucket = bucketsByScenario.get(scenarioIndex)
    if (!bucket) {
      bucket = {
        scenarioValue,
        valuesBySeries: new Map(),
      }
      bucketsByScenario.set(scenarioIndex, bucket)
      if (shouldDiscoverScenarioIndices) discoveredScenarioIndices.push(scenarioIndex)
    } else if (bucket.scenarioValue !== scenarioValue) {
      throw new ScenarioDataError(
        `Conflicting ${scenarioValueCol} for scenario ${scenarioIndex}: ${bucket.scenarioValue} vs ${scenarioValue}`,
      )
    }

    for (const column of series) {
      // A series without a value in this row is left out of its statistics.
      if (!(column in rowValues)) continue
      const value = rowValues[column]
      const values = bucket.valuesBySeries.get(column)
      if (values) {
        values.push(value)
      } else {
        bucket.valuesBySeries.set(column, [value])
      }
    }
  }

  const orderedScenarioIndices = scenarioIndices ?? discoveredScenarioIndices.sort((a, b) => a - b)
  const statsBySeries = new Map<string, ScenarioStats[]>()
  for (const column of series) {
    statsBySeries.set(
      column,
      orderedScenarioIndices.map((scenarioIndex) => {
        const bucket = bucketsByScenario.get(scenarioIndex)
        const scenarioValue = bucket?.scenarioValue ?? 0
        const values = bucket?.valuesBySeries.get(column) ?? []
        return statsFromValues(scenarioIndex, scenarioValue, values)
      }),
    )
  }
  return statsBySeries
}
