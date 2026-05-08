import type { OptimiserApplyOnlineCandidateDetail } from "../types/trace"
import { formatValue as _formatValue } from "../utils/formatValue"

const formatValue = (v: unknown) => _formatValue(v, 2)

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

export function formatSignedValue(value: number): string {
  return `${value >= 0 ? "+" : ""}${formatValue(value)}`
}

export function nextRunningTotal(total: number, contribution: number): number {
  return Number((total + contribution).toPrecision(12))
}

export function finiteRecordEntries(values: Record<string, unknown> | undefined): Array<[string, number]> {
  if (!values) return []
  return Object.entries(values).filter((entry): entry is [string, number] => isFiniteNumber(entry[1]))
}

export function optimiserDisplayCandidates(
  candidates: OptimiserApplyOnlineCandidateDetail[],
): OptimiserApplyOnlineCandidateDetail[] {
  return candidates
}

export function optimiserConstraintNames(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
  configuredConstraints: Record<string, unknown> | undefined,
): string[] {
  const names = new Set<string>()
  for (const name of Object.keys(configuredConstraints ?? {})) names.add(name)
  for (const name of Object.keys(selected?.constraints ?? {})) names.add(name)
  for (const candidate of candidates) {
    for (const name of Object.keys(candidate.constraints ?? {})) names.add(name)
    for (const name of Object.keys(candidate.lambda_terms ?? {})) names.add(name)
  }
  return [...names]
}

export function formatOptimiserRecordCell(
  values: Record<string, unknown> | undefined,
  names: string[],
  options: { signed?: boolean } = {},
): string {
  if (names.length === 0) return ""
  if (names.length === 1) {
    const value = values?.[names[0]]
    return options.signed && isFiniteNumber(value) ? formatSignedValue(value) : formatValue(value)
  }
  return names
    .map((name) => {
      const value = values?.[name]
      const formatted = options.signed && isFiniteNumber(value) ? formatSignedValue(value) : formatValue(value)
      return `${name}: ${formatted}`
    })
    .join(", ")
}

export function optimiserScoreFormulaText(
  candidate: OptimiserApplyOnlineCandidateDetail,
  objectiveLabel: string,
): string {
  const lambdaTerms = finiteRecordEntries(candidate.lambda_terms)
  if (lambdaTerms.length === 0) {
    return `score = ${objectiveLabel}`
  }
  const terms = lambdaTerms
    .map(([, value]) => formatSignedValue(value))
    .join(" ")
  return `score = ${objectiveLabel} ${terms}`
}

export function optimiserSelectedCandidate(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | null | undefined,
): OptimiserApplyOnlineCandidateDetail | undefined {
  return selected ?? candidates.find((candidate) => candidate.selected)
}

export function optimiserCandidateIsSelected(
  candidate: OptimiserApplyOnlineCandidateDetail,
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): boolean {
  return candidate.selected || selected?.scenario_index === candidate.scenario_index
}

export function optimiserScoreComparison(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): { rank: number; gapToNextBest?: number } | undefined {
  if (!selected || !isFiniteNumber(selected.decision_score)) return undefined
  const ranked = candidates
    .filter((candidate) => isFiniteNumber(candidate.decision_score))
    .sort((a, b) => {
      const scoreDiff = b.decision_score - a.decision_score
      return scoreDiff !== 0 ? scoreDiff : a.scenario_index - b.scenario_index
    })
  const rankIndex = ranked.findIndex((candidate) => candidate.scenario_index === selected.scenario_index)
  if (rankIndex < 0) return undefined
  const nextBest = ranked.find((candidate) => candidate.scenario_index !== selected.scenario_index)
  return {
    rank: rankIndex + 1,
    gapToNextBest: nextBest ? selected.decision_score - nextBest.decision_score : undefined,
  }
}

export function optimiserCandidateXValue(candidate: OptimiserApplyOnlineCandidateDetail): number {
  return isFiniteNumber(candidate.scenario_value) ? candidate.scenario_value : candidate.scenario_index
}

export type OptimiserChartCandidatePoint = {
  candidate: OptimiserApplyOnlineCandidateDetail
  xValue: number
  objectiveValue?: number
  scoreValue?: number
}

export function optimiserChartPath(candidates: OptimiserApplyOnlineCandidateDetail[]): {
  points: Array<{ candidate: OptimiserApplyOnlineCandidateDetail; x: number; y: number }>
  objectivePath: string
  scorePath: string
} {
  const numericCandidates: OptimiserChartCandidatePoint[] = candidates
    .filter((candidate) => isFiniteNumber(candidate.objective) || isFiniteNumber(candidate.decision_score))
    .map((candidate) => ({
      candidate,
      xValue: optimiserCandidateXValue(candidate),
      objectiveValue: isFiniteNumber(candidate.objective) ? candidate.objective : undefined,
      scoreValue: isFiniteNumber(candidate.decision_score) ? candidate.decision_score : undefined,
    }))
    .sort((a, b) => a.xValue - b.xValue)

  if (numericCandidates.length === 0) return { points: [], objectivePath: "", scorePath: "" }

  const width = 280
  const height = 104
  const padX = 18
  const padY = 14
  const xValues = numericCandidates.map((point) => point.xValue)
  const yValues = numericCandidates.flatMap((point) => [point.objectiveValue, point.scoreValue])
    .filter(isFiniteNumber)
  const minX = Math.min(...xValues)
  const maxX = Math.max(...xValues)
  const minY = Math.min(...yValues)
  const maxY = Math.max(...yValues)
  const xSpan = maxX - minX || 1
  const ySpan = maxY - minY || 1
  const xFor = (xValue: number) => padX + ((xValue - minX) / xSpan) * (width - padX * 2)
  const yFor = (yValue: number) => height - padY - ((yValue - minY) / ySpan) * (height - padY * 2)
  const scoreCandidates = numericCandidates.filter((point): point is OptimiserChartCandidatePoint & { scoreValue: number } =>
    isFiniteNumber(point.scoreValue)
  )
  const objectiveCandidates = numericCandidates.filter((point): point is OptimiserChartCandidatePoint & { objectiveValue: number } =>
    isFiniteNumber(point.objectiveValue)
  )
  const points = scoreCandidates.map(({ candidate, xValue, scoreValue }) => ({
    candidate,
    x: xFor(xValue),
    y: yFor(scoreValue),
  }))
  const objectivePath = objectiveCandidates
    .map((point, index) => `${index === 0 ? "M" : "L"} ${xFor(point.xValue).toFixed(1)} ${yFor(point.objectiveValue).toFixed(1)}`)
    .join(" ")
  const scorePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
    .join(" ")
  return { points, objectivePath, scorePath }
}

export function optimiserCandidateGridClass(hasConstraints: boolean): string {
  return hasConstraints
    ? "grid grid-cols-[3rem_minmax(8rem,10rem)_minmax(7rem,8.5rem)_minmax(8rem,11rem)_minmax(7rem,8.5rem)_minmax(5rem,6rem)] gap-1.5"
    : "grid grid-cols-[3rem_minmax(8rem,10rem)_minmax(7rem,8.5rem)_minmax(5rem,6rem)] gap-1.5"
}
