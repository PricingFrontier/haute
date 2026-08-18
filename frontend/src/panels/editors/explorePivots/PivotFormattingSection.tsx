import { EditorLabel } from "../../../components/form"
import {
  PIVOT_DECIMAL_PLACES_MAX,
  PIVOT_NUMBER_FORMATS,
  isNumericPivotDtype,
  isPivotFormulaPlacement,
  pivotOutputs,
} from "../../explore/pivotConfig"
import type {
  ExplorePivotConfig,
  PivotAxisPlacement,
  PivotDecimalPlaces,
  PivotFormulaPlacement,
  PivotNumberFormat,
  PivotValuePlacement,
} from "../../explore/pivotConfig"
import { effectivePivotNumberFormat } from "../../explore/pivotNumberFormat"
import { INPUT_STYLE } from "../_shared"
import type { Column } from "./placements"

type DisplayedZone = "columns" | "rows" | "values" | "formulas"

type FormattingEntry = {
  zone: DisplayedZone
  placement: PivotAxisPlacement | PivotValuePlacement | PivotFormulaPlacement
  positionLabel: string
  displayLabel: string
  numeric: boolean
}

type PivotFormattingSectionProps = {
  pivot: ExplorePivotConfig
  persistPivot: (pivot: ExplorePivotConfig) => void
  persistFormula: (formula: PivotFormulaPlacement) => void
  upstreamColumns: Column[]
}

const DECIMAL_PLACE_OPTIONS = Array.from(
  { length: PIVOT_DECIMAL_PLACES_MAX + 1 },
  (_, decimalPlaces) => decimalPlaces,
)

const NUMBER_FORMAT_LABELS: Readonly<Record<PivotNumberFormat, string>> = {
  general: "General",
  number: "Number",
  percent: "Percentage",
  currency_gbp: "Currency (£ GBP)",
  currency_usd: "Currency (US$ USD)",
  currency_eur: "Currency (€ EUR)",
}

function isNumericProducingValue(
  placement: PivotValuePlacement,
  columnsByName: ReadonlyMap<string, Column>,
): boolean {
  return (
    placement.aggregation === "count" ||
    placement.aggregation === "distinct_count" ||
    isNumericPivotDtype(columnsByName.get(placement.field)?.dtype ?? "")
  )
}

function selectedDecimalPlaces(value: string): number | null {
  if (value === "automatic") return null
  const parsed = Number(value)
  if (
    !Number.isInteger(parsed) ||
    parsed < 0 ||
    parsed > PIVOT_DECIMAL_PLACES_MAX
  ) {
    throw new Error(`Unsupported Pivot decimal places: ${value}`)
  }
  return parsed
}

function selectedNumberFormat(value: string): PivotNumberFormat {
  if (PIVOT_NUMBER_FORMATS.includes(value as PivotNumberFormat)) {
    return value as PivotNumberFormat
  }
  throw new Error(`Unsupported Pivot number format: ${value}`)
}

type FormattingChange = {
  number_format?: PivotNumberFormat
  decimal_places?: PivotDecimalPlaces
  use_grouping?: boolean
}

export default function PivotFormattingSection({
  pivot,
  persistPivot,
  persistFormula,
  upstreamColumns,
}: PivotFormattingSectionProps) {
  const columnsByName = new Map(
    upstreamColumns.map((column) => [column.name, column]),
  )
  const entries: FormattingEntry[] = [
    ...pivot.columns.map((placement, index) => ({
      zone: "columns" as const,
      placement,
      positionLabel: `Column ${index + 1}`,
      displayLabel: placement.field,
      numeric: isNumericPivotDtype(columnsByName.get(placement.field)?.dtype ?? ""),
    })),
    ...pivot.rows.map((placement, index) => ({
      zone: "rows" as const,
      placement,
      positionLabel: `Row ${index + 1}`,
      displayLabel: placement.field,
      numeric: isNumericPivotDtype(columnsByName.get(placement.field)?.dtype ?? ""),
    })),
    ...pivotOutputs(pivot).map((placement, index) => isPivotFormulaPlacement(placement)
      ? {
          zone: "formulas" as const,
          placement,
          positionLabel: `Formula ${index + 1}`,
          displayLabel: placement.display_name,
          numeric: true,
        }
      : {
          zone: "values" as const,
          placement,
          positionLabel: `Value ${index + 1}`,
          displayLabel: placement.display_name,
          numeric: isNumericProducingValue(placement, columnsByName),
        }),
  ]

  const persistFormatting = (
    entry: FormattingEntry,
    change: FormattingChange,
  ) => {
    if (entry.zone === "columns") {
      persistPivot({
        ...pivot,
        columns: pivot.columns.map((placement) =>
          placement.id === entry.placement.id
            ? { ...placement, ...change }
            : placement,
        ),
      })
      return
    }
    if (entry.zone === "rows") {
      persistPivot({
        ...pivot,
        rows: pivot.rows.map((placement) =>
          placement.id === entry.placement.id
            ? { ...placement, ...change }
            : placement,
        ),
      })
      return
    }
    if (entry.zone === "values") {
      persistPivot({
        ...pivot,
        values: pivot.values.map((placement) =>
          placement.id === entry.placement.id
            ? { ...placement, ...change }
            : placement,
        ),
      })
      return
    }
    persistFormula({
      ...(entry.placement as PivotFormulaPlacement),
      ...change,
    })
  }

  return (
    <section data-testid="pivot-formatting-section">
      <h4><EditorLabel as="span">Formatting</EditorLabel></h4>
      <div
        className="mt-1.5 rounded-lg border p-3"
        style={{ borderColor: "var(--border)", background: "var(--bg-input)" }}
      >
        {entries.length === 0 ? (
          <p className="mt-2 text-[10px]" style={{ color: "var(--text-muted)" }}>
            Add a Column, Row, Value, or Formula to format its displayed numbers.
          </p>
        ) : (
          <div className="mt-2 flex flex-col gap-2">
            {entries.map((entry) => {
            const accessibleLabel = `${entry.positionLabel} — ${entry.displayLabel}`
            const numberFormat = effectivePivotNumberFormat(entry.placement)
            const sourceField = "field" in entry.placement &&
              typeof entry.placement.field === "string"
              ? entry.placement.field
              : null
            const groupingApplies =
              numberFormat !== "general" ||
              entry.placement.decimal_places !== null &&
                entry.placement.decimal_places !== undefined
            return (
              <div
                key={`${entry.zone}:${entry.placement.id}`}
                role="group"
                aria-label={`${accessibleLabel} formatting`}
                className="flex flex-wrap items-center gap-2 rounded p-2"
                style={{ border: "1px solid var(--border)" }}
              >
                <div className="min-w-0 flex-1">
                  <div
                    className="text-[9px] font-bold uppercase tracking-wide"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {entry.positionLabel}
                  </div>
                  <div
                    className="truncate text-[11px] font-medium"
                    title={entry.displayLabel}
                  >
                    {entry.displayLabel}
                  </div>
                  {entry.zone === "values" &&
                    sourceField !== null &&
                    entry.displayLabel !== sourceField && (
                      <div className="truncate text-[9px]" style={{ color: "var(--text-muted)" }}>
                        {sourceField}
                      </div>
                    )}
                </div>

                {entry.numeric ? (
                  <div className="flex flex-wrap items-end justify-end gap-2">
                    <label className="shrink-0 text-[10px]">
                      Format
                      <select
                        aria-label={`Number format for ${accessibleLabel}`}
                        value={numberFormat}
                        onChange={(event) =>
                          persistFormatting(entry, {
                            number_format: selectedNumberFormat(event.target.value),
                          })
                        }
                        className="ml-2 rounded px-1 py-0.5 text-[10px]"
                        style={INPUT_STYLE}
                      >
                        {PIVOT_NUMBER_FORMATS.map((format) => (
                          <option key={format} value={format}>
                            {NUMBER_FORMAT_LABELS[format]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="shrink-0 text-[10px]">
                      Decimal places
                      <select
                        aria-label={`Decimal places for ${accessibleLabel}`}
                        value={entry.placement.decimal_places ?? "automatic"}
                        onChange={(event) =>
                          persistFormatting(entry, {
                            decimal_places: selectedDecimalPlaces(event.target.value),
                          })
                        }
                        className="ml-2 rounded px-1 py-0.5 text-[10px]"
                        style={INPUT_STYLE}
                      >
                        <option value="automatic">Automatic</option>
                        {DECIMAL_PLACE_OPTIONS.map((decimalPlaces) => (
                          <option key={decimalPlaces} value={decimalPlaces}>
                            {decimalPlaces}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex shrink-0 items-center gap-1 text-[10px]">
                      <input
                        type="checkbox"
                        aria-label={`Use thousands separator for ${accessibleLabel}`}
                        checked={groupingApplies && (entry.placement.use_grouping ?? true)}
                        onChange={(event) => {
                          const useGrouping = event.target.checked
                          persistFormatting(
                            entry,
                            useGrouping && !groupingApplies
                              ? { number_format: "number", use_grouping: true }
                              : { use_grouping: useGrouping },
                          )
                        }}
                        className="accent-[var(--accent)]"
                      />
                      Thousands separator (,)
                    </label>
                  </div>
                ) : (
                  <span className="shrink-0 text-[10px]" style={{ color: "var(--text-muted)" }}>
                    Not numeric
                  </span>
                )}
              </div>
            )
            })}
          </div>
        )}
      </div>
    </section>
  )
}
