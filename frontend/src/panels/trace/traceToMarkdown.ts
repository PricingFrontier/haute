import type {
  BandingNodeDetail,
  ModelScoreNodeDetail,
  OptimiserApplyNodeDetail,
  OptimiserApplyOnlineCandidateDetail,
  OptimiserApplyRatebookFactorDetail,
  RatingStepCombinedOutputDetail,
  RatingStepTableDetail,
  TraceNodeDetail,
  TraceResult,
  TraceStep,
} from "../../types/trace"
import { GENERATED_COLUMN_ORIGIN_TYPES, SOURCE_ONLY_TYPES } from "../../utils/nodeTypes"

function formatVal(v: unknown): string {
  if (v === null || v === undefined) return "null"
  if (typeof v === "number") {
    if (Number.isNaN(v)) return "NaN"
    if (v === Infinity) return "Infinity"
    if (v === -Infinity) return "-Infinity"
    return String(v)
  }
  if (typeof v === "string") return v
  return JSON.stringify(v)
}

/** Escape pipe characters so they don't break markdown tables. */
function escPipe(s: string): string {
  return s.replace(/\|/g, "\\|")
}

function isSourceOnlyNodeType(nodeType: string | undefined): boolean {
  return Boolean(nodeType && SOURCE_ONLY_TYPES.has(nodeType))
}

function isTraceOriginStep(step: TraceStep | null | undefined, tracedColumn: string | null | undefined): step is TraceStep {
  if (!step) return false
  if (isSourceOnlyNodeType(step.node_type)) return true
  return Boolean(
    tracedColumn &&
    GENERATED_COLUMN_ORIGIN_TYPES.has(step.node_type) &&
    step.schema_diff.columns_added.includes(tracedColumn),
  )
}

function isComputedPlaceholder(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "computed"
}

function ratingStepTables(detail: TraceNodeDetail): RatingStepTableDetail[] {
  return Array.isArray(detail.tables) ? detail.tables as RatingStepTableDetail[] : []
}

function ratingStepCombinedOutputs(detail: TraceNodeDetail): RatingStepCombinedOutputDetail[] {
  return Array.isArray(detail.combined_outputs) ? detail.combined_outputs as RatingStepCombinedOutputDetail[] : []
}

function ratingTableStatus(table: RatingStepTableDetail): string | undefined {
  if (typeof table.status === "string" && table.status.length > 0) return table.status
  if (table.default_used) return "default"
  if (table.matched === false) return "no_match"
  if (table.matched === true) return "matched"
  return undefined
}

function formatRatingStepDetail(detail: TraceNodeDetail): string[] {
  const parts: string[] = []
  const tables = ratingStepTables(detail)
  const combinedOutputs = ratingStepCombinedOutputs(detail)

  if (tables.length > 0) {
    const tableSummaries = tables.map((table, index) => {
      const title = table.name || table.output_column || `table ${index + 1}`
      const fields: string[] = []
      if (table.factors && table.factors.length > 0) {
        fields.push(table.factors.map((factor) => `${factor.column}=${formatVal(factor.value)}`).join(", "))
      }
      const status = ratingTableStatus(table)
      if (status) fields.push(`status=${status}`)
      if (table.selected_value !== undefined) fields.push(`selected=${formatVal(table.selected_value)}`)
      if (table.default_value !== undefined) fields.push(`default=${formatVal(table.default_value)}`)
      if (table.default_used) fields.push("default used")
      return fields.length > 0 ? `${title} (${fields.join("; ")})` : title
    })
    parts.push(`Rating tables: ${tableSummaries.join("; ")}`)
  }

  if (combinedOutputs.length > 0) {
    const combinedSummaries = combinedOutputs.map((combined) => {
      const inputs = Object.entries(combined.input_values)
        .map(([column, value]) => `${column}=${formatVal(value)}`)
        .join(", ")
      const details = [`${combined.operation} from base ${formatVal(combined.base_value)}`]
      if (inputs) details.push(inputs)
      return `${combined.column} = ${formatVal(combined.value)} (${details.join("; ")})`
    })
    parts.push(`Combined outputs: ${combinedSummaries.join("; ")}`)
  }

  return parts
}

function formatBandingDetail(detail: TraceNodeDetail): string[] {
  const banding = detail as BandingNodeDetail
  const inputColumn = banding.input_column ?? banding.column
  const matchedBand = banding.matched_band ?? banding.selected_band
  if (banding.input_value === undefined && matchedBand === undefined) {
    const factors = Array.isArray(banding.factors) ? banding.factors : []
    if (factors.length === 0) return ["Banding"]
    const factorSummaries = factors.map((factor, index) => {
      const factorInput = factor.input_column ?? factor.column
      const factorOutput = factor.output_column
      if (factorInput && factorOutput) return `${factorInput} -> ${factorOutput}`
      return factorOutput ?? factorInput ?? `factor ${index + 1}`
    })
    return [`Banding factors: ${factorSummaries.join("; ")}`]
  }
  const parts = [
    `Banding: ${inputColumn ? `${inputColumn}=` : ""}${formatVal(banding.input_value)} -> ${formatVal(matchedBand)}`,
  ]
  if (banding.is_default) parts[0] += " (default)"
  if (banding.lower_bound != null || banding.upper_bound != null) {
    const lower = banding.lower_bound != null ? formatVal(banding.lower_bound) : ""
    const upper = banding.upper_bound != null ? formatVal(banding.upper_bound) : ""
    const lowerBracket = banding.lower_inclusive === false ? "(" : "["
    const upperBracket = banding.upper_inclusive === false ? ")" : "]"
    parts[0] += ` ${lowerBracket}${lower}, ${upper}${upperBracket}`
  }
  return parts
}

function formatModelScoreDetail(detail: TraceNodeDetail): string[] {
  const model = detail as ModelScoreNodeDetail
  const parts: string[] = []
  if (model.prediction_column || model.prediction_value !== undefined) {
    const label = model.prediction_column ? `Prediction ${model.prediction_column}` : "Prediction"
    parts.push(`${label}=${formatVal(model.prediction_value)}`)
  }
  if (model.model_identity?.registered_model) {
    const version = model.model_identity.version ? ` v${model.model_identity.version}` : ""
    parts.push(`Model=${model.model_identity.registered_model}${version}`)
  } else if (model.model_identity?.run_id) {
    parts.push(`Run=${model.model_identity.run_id}`)
  }
  if (model.feature_columns && model.feature_columns.length > 0) {
    const featureValues = model.feature_values ?? {}
    parts.push(
      `Features: ${model.feature_columns
        .map((feature) => `${feature}=${formatVal(featureValues[feature])}`)
        .join(", ")}`
    )
  }
  const explanation = model.explanation
  if (explanation?.status === "error") {
    parts.push(`Explanation error: ${explanation.error ?? "unknown error"}`)
  } else if (explanation) {
    if (explanation.base_value !== undefined) {
      parts.push(`Base=${formatVal(explanation.base_value)}`)
    }
    if (explanation.prediction_from_shap !== undefined) {
      const outputSpace = explanation.output_space ? ` ${explanation.output_space}` : ""
      parts.push(`Base + contributions=${formatVal(explanation.prediction_from_shap)}${outputSpace}`)
    } else if (explanation.prediction_from_contributions !== undefined) {
      const outputSpace = explanation.output_space ? ` ${explanation.output_space}` : ""
      parts.push(`Base + contributions=${formatVal(explanation.prediction_from_contributions)}${outputSpace}`)
    }
    if (explanation.contributions && explanation.contributions.length > 0) {
      const contributionSummary = explanation.contributions
        .map((contribution) => {
          const value = contribution.contribution ?? contribution.contribution_value ?? contribution.shap_value
          const sign = value >= 0 ? "+" : ""
          const featureValue = contribution.feature_value !== undefined
            ? ` (${formatVal(contribution.feature_value)})`
            : ""
          return `${contribution.feature}${featureValue} ${sign}${formatVal(value)}`
        })
        .join(", ")
      parts.push(`Contributions: ${contributionSummary}`)
    }
  }
  return parts
}

function formatRecordEntries(values: Record<string, unknown> | undefined): string {
  if (!values || Object.keys(values).length === 0) return ""
  return Object.entries(values)
    .map(([key, value]) => `${key}=${formatVal(value)}`)
    .join(", ")
}

function formatOptimiserConstraintSettings(values: Record<string, unknown> | undefined): string {
  if (!values || Object.keys(values).length === 0) return ""
  return Object.entries(values)
    .map(([name, raw]) => {
      if (raw == null || typeof raw !== "object" || Array.isArray(raw)) {
        return `${name}=${formatVal(raw)}`
      }
      const config = raw as Record<string, unknown>
      const spec = config.spec != null && typeof config.spec === "object" && !Array.isArray(config.spec)
        ? config.spec as Record<string, unknown>
        : config
      const parts = Object.entries(spec)
        .filter(([key]) => !["lambda", "linearised_column", "lambda_term_column", "spec"].includes(key))
        .map(([key, value]) => `${key}=${formatVal(value)}`)
      if (config.lambda !== undefined) {
        parts.push(`lambda=${formatVal(config.lambda)}`)
      }
      return `${name} (${parts.join(", ")})`
    })
    .join(", ")
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function isOptimiserApplyErrorDetail(
  detail: OptimiserApplyNodeDetail,
): detail is Extract<OptimiserApplyNodeDetail, { status: "error" }> {
  return detail.status === "error"
}

function optimiserSelectedCandidate(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | null | undefined,
): OptimiserApplyOnlineCandidateDetail | undefined {
  return selected ?? candidates.find((candidate) => candidate.selected)
}

function optimiserDisplayCandidates(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): OptimiserApplyOnlineCandidateDetail[] {
  return candidates.filter((candidate) =>
    !candidate.is_baseline || selected?.scenario_index === candidate.scenario_index
  )
}

function optimiserGapToNextBestScore(
  candidates: OptimiserApplyOnlineCandidateDetail[],
  selected: OptimiserApplyOnlineCandidateDetail | undefined,
): number | undefined {
  if (!selected || !isFiniteNumber(selected.decision_score)) return undefined
  const ranked = candidates
    .filter((candidate) => isFiniteNumber(candidate.decision_score))
    .sort((a, b) => {
      const scoreDiff = b.decision_score - a.decision_score
      return scoreDiff !== 0 ? scoreDiff : a.scenario_index - b.scenario_index
    })
  const nextBest = ranked.find((candidate) => candidate.scenario_index !== selected.scenario_index)
  return nextBest ? selected.decision_score - nextBest.decision_score : undefined
}

function formatOptimiserOnlineCandidate(
  label: string,
  candidate: OptimiserApplyOnlineCandidateDetail | undefined,
  scenarioValueColumn: string,
  objectiveColumn: string,
  gapToNextBestScore?: number,
): string | undefined {
  if (!candidate) return undefined
  const fields = [
    `${scenarioValueColumn}=${formatVal(candidate.scenario_value)}`,
    `objective=${formatVal(candidate.objective)}`,
    `${objectiveColumn}=${formatVal(candidate.objective)}`,
    `score=${formatVal(candidate.decision_score)}`,
  ]
  if (isFiniteNumber(gapToNextBestScore)) {
    fields.push(`next_best_gap=${formatVal(gapToNextBestScore)}`)
  }
  const constraints = formatRecordEntries(candidate.constraints)
  if (constraints) fields.push(`constraints: ${constraints}`)
  const linearisedConstraints = formatRecordEntries(candidate.linearised_constraints)
  if (linearisedConstraints) fields.push(`linearised_constraints: ${linearisedConstraints}`)
  const lambdaTerms = formatRecordEntries(candidate.lambda_terms)
  if (lambdaTerms) fields.push(`lambda_terms: ${lambdaTerms}`)
  return `${label} scenario ${formatVal(candidate.scenario_index)} (${fields.join("; ")})`
}

function formatOptimiserScoreExplanation(
  candidate: OptimiserApplyOnlineCandidateDetail | undefined,
  objectiveColumn: string,
): string | undefined {
  if (!candidate) return undefined
  const lambdaTerms = formatRecordEntries(candidate.lambda_terms)
  const formula = lambdaTerms
    ? `score = ${objectiveColumn} + lambda terms`
    : `score = ${objectiveColumn}`
  const parts = [
    formula,
    `${objectiveColumn}=${formatVal(candidate.objective)}`,
  ]
  if (lambdaTerms) parts.push(`lambda_terms: ${lambdaTerms}`)
  parts.push(`decision_score=${formatVal(candidate.decision_score)}`)
  return `Score calculation: ${parts.join("; ")}`
}

function formatOptimiserCandidateScore(
  candidate: OptimiserApplyOnlineCandidateDetail,
  objectiveColumn: string,
  scenarioValueColumn: string,
): string {
  const lambdaTerms = formatRecordEntries(candidate.lambda_terms)
  const scoreFormula = lambdaTerms
    ? `${objectiveColumn}=${formatVal(candidate.objective)} + lambda_terms(${lambdaTerms}) = ${formatVal(candidate.decision_score)}`
    : `${objectiveColumn}=${formatVal(candidate.objective)} = ${formatVal(candidate.decision_score)}`
  const parts = [
    `candidate ${formatVal(candidate.scenario_index)}`,
    `${scenarioValueColumn}=${formatVal(candidate.scenario_value)}`,
    `score: ${scoreFormula}`,
  ]
  const constraints = formatRecordEntries(candidate.constraints)
  if (constraints) parts.push(`constraints: ${constraints}`)
  const linearisedConstraints = formatRecordEntries(candidate.linearised_constraints)
  if (linearisedConstraints) parts.push(`linearised_constraints: ${linearisedConstraints}`)
  return `${parts[0]} (${parts.slice(1).join("; ")})`
}

function formatOptimiserRatebookFactor(factor: OptimiserApplyRatebookFactorDetail): string {
  const fields = [
    `input=${formatVal(factor.input_value)}`,
    `factor=${formatVal(factor.factor_value)}`,
    `total=${formatVal(factor.running_total)}`,
    `status=${formatVal(factor.status)}`,
  ]
  if (factor.default_used) fields.push("default used")
  return `${factor.name} (${fields.join("; ")})`
}

function formatOptimiserApplyDetail(detail: TraceNodeDetail): string[] {
  const optimiser = detail as OptimiserApplyNodeDetail
  if (isOptimiserApplyErrorDetail(optimiser)) {
    const parts = [
      `Optimiser apply: ${optimiser.mode} trace failed`,
      `Error: ${optimiser.error}`,
    ]
    if (optimiser.error_type) parts.push(`Error type: ${optimiser.error_type}`)
    return parts
  }

  if (optimiser.mode === "online") {
    const candidates = Array.isArray(optimiser.candidates) ? optimiser.candidates : []
    const selectedCandidate = optimiserSelectedCandidate(candidates, optimiser.selected)
    const scenarioValueColumn = optimiser.scenario_value_column ?? "scenario"
    const objectiveColumn = optimiser.objective_column ?? "objective"
    const parts = [
      `Optimiser apply: online ${optimiser.output_column}=${formatVal(optimiser.output_value)}`,
    ]
    if (optimiser.quote_id_column) {
      parts.push(`${optimiser.quote_id_column}=${formatVal(optimiser.quote_id_value)}`)
    }
    const selected = formatOptimiserOnlineCandidate(
      "selected",
      selectedCandidate,
      scenarioValueColumn,
      objectiveColumn,
      optimiserGapToNextBestScore(candidates, selectedCandidate),
    )
    if (selected) parts.push(selected)
    const scoreExplanation = formatOptimiserScoreExplanation(selectedCandidate, objectiveColumn)
    if (scoreExplanation) parts.push(scoreExplanation)
    const constraintSettings = formatOptimiserConstraintSettings(optimiser.constraints)
    if (constraintSettings) parts.push(`Constraint settings: ${constraintSettings}`)
    const displayCandidateScores = optimiserDisplayCandidates(candidates, selectedCandidate)
      .map((candidate) => formatOptimiserCandidateScore(candidate, objectiveColumn, scenarioValueColumn))
    if (displayCandidateScores.length > 0) {
      parts.push(`Candidate scores: ${displayCandidateScores.join("; ")}`)
    }
    const optimiserLambdas = formatRecordEntries(optimiser.lambdas)
    if (optimiserLambdas) parts.push(`Lambdas: ${optimiserLambdas}`)
    parts.push(`${candidates.length} candidate${candidates.length === 1 ? "" : "s"}`)
    return parts
  }

  if (optimiser.mode === "ratebook") {
    const factors = Array.isArray(optimiser.factors) ? optimiser.factors : []
    const parts = [
      `Optimiser apply: ratebook ${optimiser.output_column}=${formatVal(optimiser.output_value)}`,
      `base=${formatVal(optimiser.base_value)}`,
    ]
    if (factors.length > 0) {
      parts.push(`factors: ${factors.map(formatOptimiserRatebookFactor).join(", ")}`)
    }
    if (optimiser.message) parts.push(`Message: ${optimiser.message}`)
    parts.push(`final=${formatVal(optimiser.final_value)}`)
    return parts
  }

  return ["Optimiser apply"]
}

/**
 * Convert a trace result into a human-readable markdown document.
 */
export function traceToMarkdown(
  trace: TraceResult,
  targetStep: TraceStep | null | undefined,
): string {
  const lines: string[] = []

  // ---- Header ----
  if (trace.column) {
    lines.push(`# Trace: ${trace.column} = ${formatVal(trace.output_value)}`)
  } else {
    lines.push("# Trace")
  }
  lines.push("")

  // Row identifier
  if (trace.row_id_column && trace.row_id_value != null) {
    lines.push(`**Row**: ${trace.row_id_column} = ${formatVal(trace.row_id_value)}`)
  } else {
    lines.push(`**Row**: Row ${trace.row_index}`)
  }
  lines.push("")

  // Metadata
  lines.push(`**Execution**: ${trace.execution_ms} ms | ${trace.steps.length} steps`)
  lines.push("")

  // ---- Formula section (only if targetStep has expression) ----
  const targetIsBanding = targetStep?.expression?.expression_type === "banding" ||
    targetStep?.node_detail?.detail_type === "banding"
  const targetIsTraceOrigin = isTraceOriginStep(targetStep, trace.column)
  const targetCalculationIsComputed = isComputedPlaceholder(targetStep?.calculation?.substituted_text)

  if (
    targetStep &&
    targetIsTraceOrigin &&
    (
      targetStep.expression == null ||
      targetStep.expression.expression_type === "opaque" ||
      targetCalculationIsComputed
    )
  ) {
    lines.push("## Source")
    lines.push("")
    lines.push(`Source node: ${targetStep.node_name}`)
    lines.push("")
  } else if (targetStep?.expression && !targetIsBanding) {
    lines.push("## Formula")
    lines.push("")
    lines.push(`\`${targetStep.expression.expression_text}\``)
    lines.push("")

    if (targetStep.calculation) {
      lines.push(`Substituted: \`${targetStep.calculation.substituted_text}\``)
      lines.push("")

      // Input values
      if (targetStep.calculation.input_values && Object.keys(targetStep.calculation.input_values).length > 0) {
        lines.push("**Inputs**:")
        for (const [key, val] of Object.entries(targetStep.calculation.input_values)) {
          lines.push(`- ${key} = ${formatVal(val)}`)
        }
        lines.push("")
      }
    }
  }

  // ---- Data Flow table ----
  const relevantSteps = trace.steps.filter((s) => s.column_relevant)
  if (relevantSteps.length > 0) {
    lines.push("## Data Flow")
    lines.push("")
    lines.push("| # | Node | Type | Details |")
    lines.push("|---|------|------|---------|")

    relevantSteps.forEach((step, idx) => {
      let details = ""
      // Schema diff summary
      const diff = step.schema_diff
      const parts: string[] = []
      if (diff.columns_added.length > 0) parts.push(`+${diff.columns_added.join(",")}`)
      if (diff.columns_modified.length > 0) parts.push(`~${diff.columns_modified.join(",")}`)
      if (diff.columns_removed.length > 0) parts.push(`-${diff.columns_removed.join(",")}`)

      const stepIsTraceOrigin = isTraceOriginStep(step, trace.column)
      const stepCalculationIsComputed = isComputedPlaceholder(step.calculation?.substituted_text)
      if (stepIsTraceOrigin && (
        step.expression == null ||
        step.expression.expression_type === "opaque" ||
        stepCalculationIsComputed
      )) {
        parts.push(`Source node: ${step.node_name}`)
      } else if (step.expression && step.node_detail?.detail_type !== "banding") {
        parts.push(step.expression.expression_text)
      }

      // Node detail
      if (step.node_detail) {
        if (
          step.node_detail.detail_type === "rating_step" &&
          (Array.isArray(step.node_detail.tables) || Array.isArray(step.node_detail.combined_outputs))
        ) {
          parts.push(...formatRatingStepDetail(step.node_detail))
        } else if (step.node_detail.detail_type === "banding") {
          parts.push(...formatBandingDetail(step.node_detail))
        } else if (step.node_detail.detail_type === "model_score") {
          parts.push(...formatModelScoreDetail(step.node_detail))
        } else if (step.node_detail.detail_type === "optimiser_apply") {
          parts.push(...formatOptimiserApplyDetail(step.node_detail))
        } else {
          for (const [key, val] of Object.entries(step.node_detail)) {
            parts.push(`${key}: ${formatVal(val)}`)
          }
        }
      }

      details = parts.map(escPipe).join("; ")

      lines.push(`| ${idx + 1} | ${escPipe(step.node_name)} | ${escPipe(step.node_type)} | ${details} |`)
    })

    lines.push("")
  }

  return lines.join("\n")
}
