import type { ExecutionMemoryPressureEvent, ExecutionMetrics, JobStatus } from "../api/types"
import { formatBytes } from "./formatBytes"

export type ExecutionDiagnostic = {
  message: string
  details: string[]
}

export type ExecutionFailureMessageOptions = {
  prefix?: string
  status?: string | null
  terminalReason?: string | null
  errorCode?: string | null
}

export type ExecutionDiagnosticOptions = {
  status?: string | null
  terminalReason?: string | null
  errorCode?: string | null
}

const PROFILE_LABELS: Record<string, string> = {
  preview_eager: "preview",
  training_prep: "training",
  optimiser_setup: "optimiser",
  optimiser_solve_worker: "optimiser",
  auto_range: "auto-range",
  lazy_sink: "sink",
  deploy_batch: "deploy",
  chunked_map_reduce: "chunked execution",
}

function profileLabel(profile: string): string {
  return PROFILE_LABELS[profile] ?? profile.replace(/_/g, " ")
}

function pressurePercent(event: ExecutionMemoryPressureEvent): number {
  if (Number.isFinite(event.threshold_percent)) return Math.round(event.threshold_percent)
  if (Number.isFinite(event.pressure_ratio)) return Math.round(event.pressure_ratio * 100)
  return Math.round(event.threshold_ratio * 100)
}

function pressureRank(event: ExecutionMemoryPressureEvent): number {
  if (Number.isFinite(event.pressure_ratio)) return event.pressure_ratio
  if (Number.isFinite(event.threshold_ratio)) return event.threshold_ratio
  return event.threshold_percent / 100
}

function highestPressureEvent(metrics: ExecutionMetrics): ExecutionMemoryPressureEvent | null {
  let highest: ExecutionMemoryPressureEvent | null = null
  for (const event of metrics.memory_pressure_events) {
    if (!highest || pressureRank(event) > pressureRank(highest)) highest = event
  }
  return highest
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value)
}

function rawErrorDetail(error: unknown): unknown {
  if (!isRecord(error)) return null
  if ("rawDetail" in error) return error.rawDetail
  return "detail" in error ? error.detail : null
}

function stringField(fields: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = fields[key]
    if (typeof value === "string" && value.trim()) return value
  }
  return null
}

export function executionErrorDetailMessage(error: unknown): string | null {
  const detail = rawErrorDetail(error)
  if (typeof detail === "string" && detail.trim()) return detail
  if (isRecord(detail)) {
    return stringField(detail, ["message", "detail", "reason", "error_code"])
  }
  return null
}

export function executionMetricsFromError(error: unknown): ExecutionMetrics | null {
  const detail = rawErrorDetail(error)
  if (!isRecord(detail) || !isRecord(detail.execution_metrics)) return null
  return detail.execution_metrics as unknown as ExecutionMetrics
}

export function executionTerminalReasonFromError(error: unknown): string | null {
  const detail = rawErrorDetail(error)
  if (isRecord(detail)) {
    const terminalReason = stringField(detail, ["terminal_reason"])
    if (terminalReason) return terminalReason
    const errorCode = stringField(detail, ["error_code"])
    if (isMemoryTerminalReason(errorCode)) return "memory_limited"
    if (errorCode && JOB_STATUSES.has(errorCode as JobStatus)) return errorCode
    const reason = stringField(detail, ["reason"])
    if (isMemoryTerminalReason(reason)) return "memory_limited"
    if (reason && JOB_STATUSES.has(reason as JobStatus)) return reason
  }
  return executionMetricsFromError(error)?.terminal_reason ?? null
}

function isMemoryTerminalReason(reason: string | null | undefined): boolean {
  return reason === "memory_limited" || reason === "memory_limit"
}

function isNonMemoryTerminalReason(reason: string | null | undefined): boolean {
  return (
    reason === "contract_error" ||
    reason === "timed_out" ||
    reason === "cancelled" ||
    reason === "superseded" ||
    reason === "error"
  )
}

function normaliseReason(reason: string | null | undefined): string | null {
  const trimmed = reason?.trim()
  return trimmed ? trimmed : null
}

function isMemoryLimitedFailure(
  metrics: ExecutionMetrics | null | undefined,
  options?: ExecutionFailureMessageOptions,
): boolean {
  return (
    isMemoryTerminalReason(normaliseReason(options?.status)) ||
    isMemoryTerminalReason(normaliseReason(options?.terminalReason)) ||
    isMemoryTerminalReason(normaliseReason(options?.errorCode)) ||
    isMemoryTerminalReason(normaliseReason(metrics?.terminal_reason))
  )
}

const JOB_STATUSES = new Set<JobStatus>([
  "running",
  "completed",
  "error",
  "cancelled",
  "superseded",
  "timed_out",
  "memory_limited",
  "contract_error",
])

export function executionJobStatusFromReason(reason: string | null | undefined): JobStatus {
  if (isMemoryTerminalReason(reason)) return "memory_limited"
  return reason && JOB_STATUSES.has(reason as JobStatus) ? reason as JobStatus : "error"
}

export function shouldShowMemoryPressureDiagnostic(
  metrics: ExecutionMetrics | null | undefined,
  options: ExecutionDiagnosticOptions = {},
): boolean {
  if (!metrics) return false
  if (isMemoryLimitedFailure(metrics, options)) return true
  if (
    isNonMemoryTerminalReason(normaliseReason(options.terminalReason)) ||
    isNonMemoryTerminalReason(normaliseReason(options.status)) ||
    isNonMemoryTerminalReason(normaliseReason(options.errorCode)) ||
    isNonMemoryTerminalReason(normaliseReason(metrics.terminal_reason)) ||
    isNonMemoryTerminalReason(normaliseReason(metrics.status))
  ) {
    return false
  }
  return highestPressureEvent(metrics) !== null
}

export function buildExecutionDiagnostic(
  metrics: ExecutionMetrics | null | undefined,
  options: ExecutionDiagnosticOptions = {},
): ExecutionDiagnostic | null {
  if (!shouldShowMemoryPressureDiagnostic(metrics, options) || !metrics) return null
  const event = highestPressureEvent(metrics)
  if (!event) return null

  const details = [
    `RSS ${formatBytes(event.rss_bytes)} of ${formatBytes(event.rss_limit_bytes)} limit`,
    `Headroom used ${formatBytes(event.headroom_used_bytes)} of ${formatBytes(event.headroom_bytes)}`,
  ]
  if (event.stage) details.push(`Stage ${event.stage}`)
  if (event.config_key) details.push(`Budget ${event.config_key}`)

  return {
    message: `Memory pressure reached ${pressurePercent(event)}% of the ${profileLabel(metrics.profile)} budget.`,
    details,
  }
}

export function buildExecutionFailureMessage(
  baseMessage: string | null | undefined,
  metrics: ExecutionMetrics | null | undefined,
  options: string | ExecutionFailureMessageOptions = {},
): string {
  const resolvedOptions: ExecutionFailureMessageOptions =
    typeof options === "string" ? { prefix: options } : options
  const prefix = resolvedOptions.prefix
  if (!isMemoryLimitedFailure(metrics, resolvedOptions)) {
    return baseMessage?.trim() || prefix || "Execution failed"
  }

  const diagnostic = buildExecutionDiagnostic(metrics)
  if (!diagnostic) return baseMessage?.trim() || prefix || "Execution failed"

  const firstDetail = diagnostic.details[0]
  const message = firstDetail
    ? `${diagnostic.message} ${firstDetail}.`
    : diagnostic.message
  return prefix ? `${prefix}: ${message.charAt(0).toLowerCase()}${message.slice(1)}` : message
}
