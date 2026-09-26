import type { OptimiserSolveResult } from "../../api/types"

export const EXPECTED_VALUES_STATEMENT =
  "Expected values from the scoring models on the solve quotes; not observed outcomes."

const MODE_LABELS: Record<string, string> = {
  online: "Online",
  ratebook: "Ratebook",
}

/**
 * The provenance strip's segments: the mode, the grid solved, the data the
 * solve ran on (its `input_summary`), which result the figures belong to, and
 * what kind of figures they are. `pointCount` is the number of frontier points
 * returned, which the selected index counts within.
 */
export function optimiserResultProvenance(
  result: OptimiserSolveResult,
  selectedPointIndex: number | null,
  pointCount: number,
): string[] {
  const mode = MODE_LABELS[result.mode]
  if (!mode) throw new Error(`Unknown optimiser mode "${result.mode}"`)
  const grid = result.n_quotes != null && result.n_steps != null
    ? `${result.n_quotes.toLocaleString()} quotes × ${result.n_steps.toLocaleString()} scenario steps`
    : "Grid size not reported"
  const { data_source: dataSource, source_file: sourceFile } = result.input_summary
  const source = sourceFile == null
    ? `Data: ${dataSource} scenario`
    : `Data: ${dataSource} scenario of ${sourceFile}`
  if (selectedPointIndex != null && !(selectedPointIndex >= 0 && selectedPointIndex < pointCount)) {
    throw new Error(
      `Selected frontier point ${selectedPointIndex} is outside the ${pointCount} points returned`,
    )
  }
  const target = selectedPointIndex == null
    ? "As solved"
    : `Frontier point ${selectedPointIndex + 1} of ${pointCount.toLocaleString()}`
  return [mode, grid, source, target, EXPECTED_VALUES_STATEMENT]
}
