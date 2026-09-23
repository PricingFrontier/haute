import { ConfigSection } from "../../components/form"
import { CHART_COLORS } from "../../theme/colors"
import type { EvaluationPreview } from "../../api/types"
import {
  compatibleEvaluationPreview,
  type EvaluationStrategy,
  type ValidationMethod,
} from "./evaluationPreview"

const format = (rows: number) => rows.toLocaleString()

type Segment = {
  label: string
  value: number
  color: string
  percentage: boolean
}

function Bar({ entries }: { entries: Segment[] }) {
  const total = entries.reduce((sum, entry) => sum + entry.value, 0)
  return (
    <>
      <div
        className="mt-2 flex h-3 overflow-hidden rounded"
        style={{ background: "var(--chrome-hover)" }}
      >
        {entries.map((entry) => (
          <div
            key={entry.label}
            style={{
              width: `${(entry.value / total) * 100}%`,
              background: entry.color,
            }}
          />
        ))}
      </div>
      <div
        className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs"
        style={{ color: "var(--text-secondary)" }}
      >
        {entries.map((entry) => (
          <span key={entry.label}>
            <i
              className="mr-1 inline-block h-2 w-2 rounded-sm"
              style={{ background: entry.color }}
            />
            {entry.percentage
              ? `${entry.value.toFixed(0)}% ${entry.label}`
              : `${format(entry.value)} ${entry.label} (${((entry.value / total) * 100).toFixed(0)}%)`}
          </span>
        ))}
      </div>
    </>
  )
}

function range(minimum: number | undefined, maximum: number | undefined) {
  if (minimum === undefined && maximum === undefined) return null
  if (minimum === undefined) return `max ${format(maximum!)}`
  if (maximum === undefined) return `min ${format(minimum)}`
  return minimum === maximum
    ? format(minimum)
    : `${format(minimum)}–${format(maximum)}`
}

function PerFitRanges({ preview }: { preview: EvaluationPreview | null }) {
  if (preview === null) return null
  const training = range(
    preview.min_selection_train_rows,
    preview.max_selection_train_rows,
  )
  const validation = range(
    preview.min_selection_validation_rows,
    preview.max_selection_validation_rows,
  )
  if (training === null && validation === null) return null
  return (
    <div
      className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs"
      style={{ color: "var(--text-muted)" }}
    >
      {training !== null && <span>Training rows per fit: {training}</span>}
      {validation !== null && (
        <span>Validation rows per fit: {validation}</span>
      )}
    </div>
  )
}

export function EvaluationAllocation({
  strategy,
  method,
  validationSize,
  testSize,
  hasTest,
  preview,
  invalid,
}: {
  strategy: EvaluationStrategy
  method: ValidationMethod
  validationSize: number
  testSize: number
  hasTest: boolean
  preview: EvaluationPreview | null
  invalid: boolean
}) {
  if (invalid) return null
  const exact = compatibleEvaluationPreview(preview, strategy, method)
  const colors = [CHART_COLORS.train, "var(--accent)", "var(--signif-high)"]
  const training = exact?.min_selection_train_rows
  const validation = exact?.min_selection_validation_rows
  const exactSingle =
    method === "single" &&
    training !== undefined &&
    training === exact?.max_selection_train_rows &&
    validation !== undefined &&
    validation === exact?.max_selection_validation_rows
  const title = exact ? "Exact allocation" : "Target allocation"

  if (strategy === "temporal" && method === "cross_validation") {
    if (exact === null) {
      return (
        <ConfigSection
          title={`Expanding-window CV · ${validationSize} folds`}
          ariaLabel="Data allocation"
        >
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            Awaiting preview
          </div>
        </ConfigSection>
      )
    }
    const entries: Segment[] = [
      {
        label: "Training set",
        value: exact.development_rows,
        color: colors[0],
        percentage: false,
      },
      ...(hasTest
        ? [
            {
              label: "Test set",
              value: exact.final_test_rows,
              color: colors[2],
              percentage: false,
            },
          ]
        : []),
    ]
    return (
      <ConfigSection
        title={`Expanding-window CV · ${validationSize} folds`}
        ariaLabel="Data allocation"
      >
        <Bar entries={entries} />
        <PerFitRanges preview={exact} />
      </ConfigSection>
    )
  }

  if (
    strategy === "temporal" &&
    ((method === "single" && !exactSingle) ||
      (method === "none" && exact === null))
  ) {
    return (
      <ConfigSection title="Data allocation" ariaLabel="Data allocation">
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>
          Awaiting preview
        </div>
      </ConfigSection>
    )
  }

  if (method === "cross_validation") {
    const finalTestFraction = hasTest
      ? exact === null
        ? testSize
        : exact.final_test_rows /
          (exact.development_rows + exact.final_test_rows)
      : 0
    return (
      <ConfigSection title={title} ariaLabel="Data allocation">
        <div className="mt-2 flex h-3 gap-0.5">
          <div
            className="flex gap-0.5"
            style={{ width: `${(1 - finalTestFraction) * 100}%` }}
          >
            {Array.from(
              { length: Math.max(2, Math.min(10, validationSize)) },
              (_, index) => (
                <i
                  key={index}
                  className="flex-1 rounded-sm"
                  style={{ background: colors[0] }}
                />
              ),
            )}
          </div>
          {hasTest && (
            <i
              style={{
                width: `${finalTestFraction * 100}%`,
                background: colors[2],
              }}
            />
          )}
        </div>
        <div
          className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs"
          style={{ color: "var(--text-secondary)" }}
        >
          <span>{validationSize} CV folds</span>
          <span>
            {exact
              ? `${format(exact.development_rows)} Training set (${((exact.development_rows / (exact.development_rows + exact.final_test_rows)) * 100).toFixed(0)}%)`
              : `${((1 - testSize) * 100).toFixed(0)}% Training set`}
          </span>
          {hasTest && (
            <span>
              {exact
                ? `${format(exact.final_test_rows)} Test set (${((exact.final_test_rows / (exact.development_rows + exact.final_test_rows)) * 100).toFixed(0)}%)`
                : `${(testSize * 100).toFixed(0)}% Test set`}
            </span>
          )}
        </div>
        <PerFitRanges preview={exact} />
      </ConfigSection>
    )
  }

  const entries: Segment[] = exactSingle
    ? [
        {
          label: "Training set",
          value: training!,
          color: colors[0],
          percentage: false,
        },
        {
          label: "Validation set",
          value: validation!,
          color: colors[1],
          percentage: false,
        },
        ...(hasTest
          ? [
              {
                label: "Test set",
                value: exact!.final_test_rows,
                color: colors[2],
                percentage: false,
              },
            ]
          : []),
      ]
    : method === "none" && exact !== null
      ? [
          {
            label: "Training set",
            value: exact.development_rows,
            color: colors[0],
            percentage: false,
          },
          ...(hasTest
            ? [
                {
                  label: "Test set",
                  value: exact.final_test_rows,
                  color: colors[2],
                  percentage: false,
                },
              ]
            : []),
        ]
      : method === "none"
        ? [
            {
              label: "Training set",
              value: 100 - testSize * 100,
              color: colors[0],
              percentage: true,
            },
            ...(hasTest
              ? [
                  {
                    label: "Test set",
                    value: testSize * 100,
                    color: colors[2],
                    percentage: true,
                  },
                ]
              : []),
          ]
        : [
            {
              label: "Training set",
              value: (1 - validationSize - testSize) * 100,
              color: colors[0],
              percentage: true,
            },
            {
              label: "Validation set",
              value: validationSize * 100,
              color: colors[1],
              percentage: true,
            },
            ...(hasTest
              ? [
                  {
                    label: "Test set",
                    value: testSize * 100,
                    color: colors[2],
                    percentage: true,
                  },
                ]
              : []),
          ]
  return (
    <ConfigSection title={title} ariaLabel="Data allocation">
      <Bar entries={entries} />
    </ConfigSection>
  )
}
