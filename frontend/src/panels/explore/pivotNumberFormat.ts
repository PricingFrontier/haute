import type {
  PivotDecimalPlaces,
  PivotNumberFormat,
} from "./pivotConfig"

export type PivotNumberFormatting = {
  number_format?: PivotNumberFormat
  decimal_places?: PivotDecimalPlaces
  use_grouping?: boolean
}

const DECIMAL_PARTS_PATTERN = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:[eE]([+-]?[0-9]+))?$/
const MAX_DECIMAL_SHIFT = 10_000

type DecimalParts = {
  negative: boolean
  digits: string
  scale: number
}

function parseDecimalParts(value: unknown): DecimalParts | null {
  if (
    (typeof value !== "number" && typeof value !== "string") ||
    (typeof value === "number" && !Number.isFinite(value))
  ) {
    return null
  }
  const raw = String(value)
  const match = DECIMAL_PARTS_PATTERN.exec(raw)
  if (!match) return null

  const fraction = match[3] ?? ""
  const exponent = Number(match[4] ?? 0)
  if (!Number.isSafeInteger(exponent)) {
    throw new Error(`Pivot numeric exponent is outside the supported range: ${raw}`)
  }
  const scale = fraction.length - exponent
  if (Math.abs(scale) > MAX_DECIMAL_SHIFT) {
    throw new Error(`Pivot numeric exponent is too large to display: ${raw}`)
  }
  return {
    negative: match[1] === "-",
    digits: `${match[2]}${fraction}`,
    scale,
  }
}

function groupedInteger(value: string, useGrouping: boolean): string {
  const normalized = value.replace(/^0+(?=[0-9])/, "")
  return useGrouping
    ? normalized.replace(/\B(?=(?:[0-9]{3})+(?![0-9]))/g, ",")
    : normalized
}

function roundedMagnitude(parts: DecimalParts, decimalPlaces: number): bigint {
  const shift = decimalPlaces - parts.scale
  if (Math.abs(shift) > MAX_DECIMAL_SHIFT) {
    throw new Error("Pivot numeric exponent is too large to display.")
  }
  const coefficient = BigInt(parts.digits)
  if (shift >= 0) return coefficient * 10n ** BigInt(shift)

  const divisor = 10n ** BigInt(-shift)
  const quotient = coefficient / divisor
  const remainder = coefficient % divisor
  return remainder * 2n >= divisor ? quotient + 1n : quotient
}

function fixedMagnitudeLabel(
  magnitude: bigint,
  decimalPlaces: number,
  useGrouping: boolean,
  trimTrailingZeroes = false,
): string {
  if (decimalPlaces === 0) {
    return groupedInteger(magnitude.toString(), useGrouping)
  }
  const digits = magnitude.toString().padStart(decimalPlaces + 1, "0")
  const whole = groupedInteger(digits.slice(0, -decimalPlaces), useGrouping)
  let fraction = digits.slice(-decimalPlaces)
  if (trimTrailingZeroes) fraction = fraction.replace(/0+$/, "")
  return fraction ? `${whole}.${fraction}` : whole
}

function exactMagnitudeLabel(
  parts: DecimalParts,
  useGrouping: boolean,
): string {
  if (parts.scale <= 0) {
    return groupedInteger(
      `${parts.digits}${"0".repeat(-parts.scale)}`,
      useGrouping,
    )
  }
  const digits = parts.digits.padStart(parts.scale + 1, "0")
  const whole = groupedInteger(digits.slice(0, -parts.scale), useGrouping)
  return `${whole}.${digits.slice(-parts.scale)}`
}

function decoratedLabel(
  numericLabel: string,
  negative: boolean,
  format: PivotNumberFormat,
): string {
  const sign = negative ? "-" : ""
  if (format === "percent") return `${sign}${numericLabel}%`
  const symbol = format === "currency_gbp"
    ? "£"
    : format === "currency_usd"
      ? "US$"
      : format === "currency_eur"
        ? "€"
        : ""
  return `${sign}${symbol}${numericLabel}`
}

export function effectivePivotNumberFormat(
  formatting: PivotNumberFormatting,
): PivotNumberFormat {
  // Absent formatting only occurs for sources without persisted settings (a
  // filter moving into a displayed zone, or a retained result whose value no
  // longer exists in the pivot); parsed placements always carry the full trio.
  return formatting.number_format ?? "general"
}

export function formatPivotNumber(
  value: unknown,
  formatting: PivotNumberFormatting,
): string | null {
  const parts = parseDecimalParts(value)
  if (!parts) return null

  const format = effectivePivotNumberFormat(formatting)
  const decimalPlaces = formatting.decimal_places ?? null
  const useGrouping = formatting.use_grouping ?? true
  if (format === "general" && decimalPlaces === null) return String(value)

  const adjustedParts = format === "percent"
    ? { ...parts, scale: parts.scale - 2 }
    : parts
  let numericLabel: string
  if (decimalPlaces !== null) {
    numericLabel = fixedMagnitudeLabel(
      roundedMagnitude(adjustedParts, decimalPlaces),
      decimalPlaces,
      useGrouping,
    )
  } else if (
    format === "currency_gbp" ||
    format === "currency_usd" ||
    format === "currency_eur"
  ) {
    numericLabel = fixedMagnitudeLabel(
      roundedMagnitude(adjustedParts, 2),
      2,
      useGrouping,
    )
  } else if (format === "percent") {
    numericLabel = fixedMagnitudeLabel(
      roundedMagnitude(adjustedParts, 2),
      2,
      useGrouping,
      true,
    )
  } else {
    numericLabel = exactMagnitudeLabel(adjustedParts, useGrouping)
  }
  return decoratedLabel(numericLabel, adjustedParts.negative, format)
}
