import { useEffect, useId, useMemo, useRef, useState, type CSSProperties } from "react"
import { Plus, X } from "lucide-react"
import {
  CommittedTextArea,
  CommittedTextField,
  EditorLabel,
} from "../../components/form"
import type {
  IoCapabilityGroup,
  IoFormatCapability,
  IoInputCapability,
  IoOutputCapability,
} from "../../api/types"
import { withAlpha } from "../../utils/color"
import { INPUT_STYLE } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import PathPickerField from "./shared/PathPickerField"

type Direction = "input" | "output"

const INPUT_COMMON_KEYS = new Set([
  "instanceOf",
  "inputMapping",
  "selected_columns",
  "column_renames",
  "categorical_levels",
  "contract",
  "code",
])

const OUTPUT_COMMON_KEYS = new Set([
  "instanceOf",
  "inputMapping",
  "selected_columns",
  "column_renames",
  "categorical_levels",
  "contract",
])

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

let nextArgumentRowId = 0

type ArgumentRow = {
  id: number
  name: string
  valueText: string
}

function argumentRows(args: Record<string, unknown>): ArgumentRow[] {
  return Object.entries(args).map(([name, value]) => ({
    id: nextArgumentRowId++,
    name,
    valueText: JSON.stringify(value) ?? "null",
  }))
}

function parsesAsJson(value: string): boolean {
  try {
    JSON.parse(value)
    return true
  } catch {
    return false
  }
}

export function IoArgumentsEditor({
  value,
  argumentNames,
  context,
  onCommit,
  inputStyle = INPUT_STYLE,
}: {
  value: unknown
  argumentNames: string[]
  context: string
  onCommit: (next: Record<string, unknown>) => void
  inputStyle?: CSSProperties
}) {
  const args = useMemo(() => (isPlainRecord(value) ? value : {}), [value])
  const argsJson = JSON.stringify(args)
  const [rows, setRows] = useState<ArgumentRow[]>(() => argumentRows(args))
  const lastSynced = useRef(argsJson)
  const datalistId = useId()

  useEffect(() => {
    if (argsJson !== lastSynced.current) {
      lastSynced.current = argsJson
      setRows(argumentRows(args))
    }
  }, [args, argsJson])

  const commit = (nextRows: ArgumentRow[]) => {
    setRows(nextRows)
    const next: Record<string, unknown> = {}
    for (const row of nextRows) {
      const name = row.name.trim()
      if (!name) continue
      try {
        next[name] = JSON.parse(row.valueText)
      } catch {
        if (Object.hasOwn(args, name)) next[name] = args[name]
      }
    }
    lastSynced.current = JSON.stringify(next)
    onCommit(next)
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <EditorLabel as="span">Arguments</EditorLabel>
        <button
          type="button"
          onClick={() =>
            commit([
              ...rows,
              { id: nextArgumentRowId++, name: "", valueText: "" },
            ])
          }
          className="flex items-center gap-1 text-[11px] font-medium"
          style={{ color: "var(--accent)" }}
        >
          <Plus size={11} />
          Add argument
        </button>
      </div>

      <datalist id={datalistId}>
        {argumentNames.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>

      {rows.length === 0 ? (
        <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
          No arguments configured.
        </p>
      ) : (
        <div className="mt-1 space-y-1.5">
          {rows.map((row, index) => {
            const name = row.name.trim()
            const unknownName =
              name !== "" && !argumentNames.includes(name)
            const invalidValue =
              row.valueText !== "" && !parsesAsJson(row.valueText)
            return (
              <div key={row.id}>
                <div className="flex items-center gap-1.5">
                  <CommittedTextField
                    type="text"
                    list={datalistId}
                    value={row.name}
                    placeholder="name"
                    aria-label={`Argument ${index + 1} name`}
                    onCommit={(nextName) =>
                      commit(
                        rows.map((candidate) =>
                          candidate.id === row.id
                            ? { ...candidate, name: nextName }
                            : candidate,
                        ),
                      )
                    }
                    className="focus-ring w-2/5 px-2 py-1.5 text-xs font-mono rounded-lg"
                    style={inputStyle}
                  />
                  <CommittedTextField
                    type="text"
                    value={row.valueText}
                    placeholder='JSON value, e.g. "," or true'
                    aria-label={`Argument ${index + 1} value`}
                    onCommit={(valueText) =>
                      commit(
                        rows.map((candidate) =>
                          candidate.id === row.id
                            ? { ...candidate, valueText }
                            : candidate,
                        ),
                      )
                    }
                    className="focus-ring flex-1 px-2 py-1.5 text-xs font-mono rounded-lg"
                    style={inputStyle}
                  />
                  <button
                    type="button"
                    aria-label={`Remove argument ${index + 1}`}
                    onClick={() =>
                      commit(rows.filter((candidate) => candidate.id !== row.id))
                    }
                    className="icon-danger-btn p-1 rounded shrink-0"
                  >
                    <X size={12} />
                  </button>
                </div>
                {unknownName && (
                  <p
                    className="mt-0.5 text-[11px]"
                    style={{ color: "var(--warning-strong)" }}
                  >
                    {name} is not a supported {context} argument. It is kept so
                    the backend can reject it explicitly.
                  </p>
                )}
                {invalidValue && (
                  <p
                    className="mt-0.5 text-[11px]"
                    style={{ color: "var(--danger-text)" }}
                  >
                    Invalid JSON. The previous value is kept until this parses.
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function RecordsField({
  fieldName,
  label,
  required,
  config,
  onUpdate,
  inputStyle,
}: {
  fieldName: string
  label: string
  required: boolean
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputStyle: CSSProperties
}) {
  const raw = config[fieldName]
  const configText = JSON.stringify(raw ?? [], null, 2) ?? "[]"
  const configError =
    raw === undefined || Array.isArray(raw) ? null : "Records must be a JSON array."
  const [editor, setEditor] = useState(() => ({
    sourceText: configText,
    text: configText,
    error: configError,
  }))
  const id = useId()

  // React permits a guarded render-time adjustment when state is derived from
  // a prop identity. This keeps undo/external config replacement in sync
  // without an effect-driven extra render, while preserving an in-progress
  // draft until the persisted value actually changes.
  if (editor.sourceText !== configText) {
    setEditor({
      sourceText: configText,
      text: configText,
      error: configError,
    })
  }

  const parseRecords = (value: string): unknown[] | null => {
    let parsed: unknown
    try {
      parsed = JSON.parse(value)
    } catch {
      setEditor((current) => ({
        ...current,
        error: "Invalid JSON - changes have not been saved.",
      }))
      return null
    }
    if (!Array.isArray(parsed)) {
      setEditor((current) => ({
        ...current,
        error: "Records must be a JSON array.",
      }))
      return null
    }
    setEditor((current) => ({ ...current, error: null }))
    return parsed
  }

  return (
    <div>
      <EditorLabel htmlFor={id}>
        {label}
        {required ? " *" : ""}
      </EditorLabel>
      <textarea
        id={id}
        aria-label={label}
        value={editor.text}
        rows={6}
        placeholder='[{"column": "value"}]'
        onChange={(event) => {
          setEditor((current) => ({
            ...current,
            text: event.target.value,
          }))
          parseRecords(event.target.value)
        }}
        onBlur={() => {
          const parsed = parseRecords(editor.text)
          if (parsed === null) return
          const canonical = JSON.stringify(parsed, null, 2)
          setEditor({ sourceText: canonical, text: canonical, error: null })
          if (canonical === configText) return
          onUpdate(fieldName, parsed)
        }}
        className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg resize-y"
        style={inputStyle}
      />
      {editor.error && (
        <p className="mt-1 text-[11px]" style={{ color: "var(--danger-text)" }}>
          {editor.error}
        </p>
      )}
    </div>
  )
}

function CapabilityDiagnostics({
  direction,
  capability,
}: {
  direction: Direction
  capability: IoInputCapability | IoOutputCapability
}) {
  if (direction === "input") {
    return null
  }

  const output = capability as IoOutputCapability
  const execution =
    output.native_sink && output.eager_writer
      ? "streaming sink or eager writer"
      : output.native_sink
        ? "streaming sink"
        : "eager writer"
  return (
    <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
      {execution}; {output.publication} publication.
    </p>
  )
}

export default function IoFormatEditor({
  group,
  direction,
  config,
  onUpdate,
  onSelectFormat,
  accentColor,
}: {
  group: IoCapabilityGroup
  direction: Direction
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onSelectFormat: (format: IoFormatCapability) => void
  accentColor: string
}) {
  const formats = group.formats.filter(
    (candidate) => candidate[direction] !== null,
  )
  const format = formats.find(
    (candidate) => candidate.name === config.format,
  )
  const capability = format?.[direction] ?? null
  const modes = capability?.modes ?? []
  const explicitMode =
    typeof config.mode === "string" ? config.mode : ""
  const effectiveMode = explicitMode || modes[0] || ""
  const fields =
    direction === "input" ? group.input_fields : group.output_fields
  const modeVisible =
    modes.length > 1 || (direction === "output" && explicitMode !== "")
  const formatId = useId()
  const modeId = useId()

  const inputStyle: CSSProperties = {
    ...INPUT_STYLE,
    ["--focus-ring-border" as string]: withAlpha(accentColor, 0.3),
    ["--focus-ring-shadow" as string]: withAlpha(accentColor, 0.1),
  }

  const configErrors: string[] = []
  if (!format) {
    configErrors.push("Select a valid format for this provider.")
  }
  if (
    capability &&
    explicitMode !== "" &&
    !modes.includes(explicitMode as never)
  ) {
    configErrors.push("The selected mode is not valid for this format.")
  }
  if (
    config.arguments !== undefined &&
    !isPlainRecord(config.arguments)
  ) {
    configErrors.push("Arguments must be an object.")
  }

  const common =
    direction === "input" ? INPUT_COMMON_KEYS : OUTPUT_COMMON_KEYS
  const knownKeys = new Set<string>([
    ...common,
    direction === "input" ? "inputType" : "outputType",
    "format",
    "arguments",
    ...fields.map((field) => field.name),
  ])
  if (direction === "input") knownKeys.add("cacheMode")
  if (!(direction === "input" && group.name === "database")) {
    knownKeys.add("mode")
  }
  const inactiveKeys = Object.keys(config).filter(
    (key) => config[key] !== undefined && !knownKeys.has(key),
  )
  if (inactiveKeys.length > 0) {
    configErrors.push(
      `Unexpected configuration keys: ${inactiveKeys.join(", ")}.`,
    )
  }

  const updateField = (name: string, value: string) => {
    if ((name === "connection" || name === "uri") && value.trim() !== "") {
      const other = name === "connection" ? "uri" : "connection"
      onUpdate({ [name]: value, [other]: "" })
      return
    }
    onUpdate(name, value)
  }

  const argumentMode =
    direction === "input" && group.name === "database"
      ? "snapshot"
      : effectiveMode
  const argumentNames = capability?.arguments[argumentMode] ?? []

  return (
    <div className="space-y-3">
      {configErrors.length > 0 && (
        <section
          aria-label="Configuration errors"
          className="rounded-lg p-2 text-[11px]"
          style={{
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
            color: "var(--danger-text)",
          }}
        >
          <EditorLabel as="div" color="var(--danger-text)">
            Configuration errors
          </EditorLabel>
          {configErrors.map((message) => (
            <p key={message}>{message}</p>
          ))}
        </section>
      )}

      <div>
        <EditorLabel htmlFor={formatId}>Format</EditorLabel>
        <select
          id={formatId}
          aria-label="Format"
          value={typeof config.format === "string" ? config.format : ""}
          onChange={(event) => {
            const next = formats.find(
              (candidate) => candidate.name === event.target.value,
            )
            if (next) onSelectFormat(next)
          }}
          className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs rounded-lg"
          style={inputStyle}
        >
          <option value="">Select a format...</option>
          {typeof config.format === "string" &&
            config.format !== "" &&
            !formats.some((candidate) => candidate.name === config.format) && (
              <option value={config.format}>
                {config.format} (not available)
              </option>
            )}
          {formats.map((candidate) => (
            <option key={candidate.name} value={candidate.name}>
              {candidate.label}
              {candidate.unstable ? " (unstable)" : ""}
              {candidate[direction]?.engines_missing.length
                ? ` - needs one of: ${candidate[
                    direction
                  ]?.engines_missing.join(", ")}`
                : ""}
            </option>
          ))}
        </select>

        {format?.unstable && (
          <p
            className="mt-1 text-[11px]"
            style={{ color: "var(--warning-strong)" }}
          >
            Polars marks this format's{" "}
            {direction === "input" ? "reader" : "writer"} as unstable.
          </p>
        )}
        {capability && capability.engines_missing.length > 0 && (
          <p
            className="mt-1 rounded-lg px-2.5 py-1.5 text-[11px]"
            style={{
              background: "var(--warning-soft)",
              border: "1px solid var(--warning-border)",
              color: "var(--warning-strong)",
            }}
          >
            Missing engine package. Install one of:{" "}
            {capability.engines_missing.join(", ")}.
          </p>
        )}
      </div>

      {modeVisible && (
        <div>
          <EditorLabel htmlFor={modeId}>Mode</EditorLabel>
          <select
            id={modeId}
            aria-label="Mode"
            value={effectiveMode}
            onChange={(event) => onUpdate("mode", event.target.value)}
            className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs rounded-lg"
            style={inputStyle}
          >
            {explicitMode !== "" &&
              !modes.includes(explicitMode as never) && (
                <option value={explicitMode}>
                  {explicitMode} (not valid for this format)
                </option>
              )}
            {modes.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </div>
      )}

      {fields.map((field) => {
        if (field.kind === "records") {
          return (
            <RecordsField
              key={field.name}
              fieldName={field.name}
              label={field.label}
              required={field.required}
              config={config}
              onUpdate={onUpdate}
              inputStyle={inputStyle}
            />
          )
        }

        const rawValue = config[field.name]
        const value = typeof rawValue === "string" ? rawValue : ""

        if (field.kind === "path") {
          return (
            <PathPickerField
              key={field.name}
              label={`${field.label}${field.required ? " *" : ""}`}
              value={value}
              onSelect={(path) => updateField(field.name, path)}
              extensions={format && format.extensions.length > 0 ? format.extensions.join(",") : undefined}
              manualEntry={direction === "output"}
            />
          )
        }

        return (
          <div key={field.name}>
            <EditorLabel>
              {field.label}
              {field.required ? " *" : ""}
            </EditorLabel>
            {field.kind === "query" ? (
              <CommittedTextArea
                aria-label={field.label}
                value={value}
                rows={3}
                onCommit={(next) => updateField(field.name, next)}
                className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg resize-y"
                style={inputStyle}
              />
            ) : (
              <CommittedTextField
                aria-label={field.label}
                value={value}
                onCommit={(next) => updateField(field.name, next)}
                className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
                style={inputStyle}
              />
            )}
          </div>
        )
      })}

      {capability && (
        <>
          <CapabilityDiagnostics
            direction={direction}
            capability={capability}
          />
          <IoArgumentsEditor
            value={config.arguments}
            argumentNames={argumentNames}
            context={`${format?.label ?? "format"} ${argumentMode}`.trim()}
            onCommit={(next) => onUpdate("arguments", next)}
            inputStyle={inputStyle}
          />
        </>
      )}
    </div>
  )
}
