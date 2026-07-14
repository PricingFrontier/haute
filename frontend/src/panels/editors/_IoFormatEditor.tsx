/**
 * Shared editor body for the dataInput / dataOutput node editors.
 *
 * Registry-driven (io-nodes review IO12): every format option, mode
 * option, argument-name list and missing-engine flag comes from the
 * GET /api/formats capability payload — no format knowledge is
 * hard-coded here. Formats with missing engine packages stay selectable
 * but are flagged with the reason.
 *
 * 1:1 JSON↔UI invariant: every key present in the persisted config
 * surfaces somewhere visible — keys this editor does not recognise (or
 * that do not apply to the selected format's source kind) render in a
 * read-only "Unrecognised keys" section rather than being dropped.
 */
import { useEffect, useId, useRef, useState } from "react"
import { Plus, X } from "lucide-react"
import { EditorLabel } from "../../components/form"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { FileBrowser, INPUT_STYLE } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import { IO_SIDE_SPECS, useIoFormats } from "./_ioFormats"
import type { IoSide } from "./_ioFormats"

// ─── Argument rows ────────────────────────────────────────────────

// Row ids are unique across the session so React keys stay stable while
// rows are renamed or edited.
let nextArgRowId = 0

type ArgRow = { id: number; name: string; valueText: string }

function rowsFromArguments(args: Record<string, unknown>): ArgRow[] {
  return Object.entries(args).map(([name, value]) => ({
    id: nextArgRowId++,
    name,
    valueText: JSON.stringify(value),
  }))
}

function parsesAsJson(text: string): boolean {
  try {
    JSON.parse(text)
    return true
  } catch {
    return false
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/**
 * Rows of (name, JSON value) for the config's `arguments` object. Names
 * are validated against the payload's argument list for the selected
 * format+mode: unknown names are flagged inline but still persisted —
 * fail-loud happens at execute time. Values must be JSON; an invalid
 * value keeps the previously persisted value until it parses.
 */
function ArgumentsEditor({
  args,
  argumentNames,
  flagContext,
  onCommit,
  inputStyle,
}: {
  args: Record<string, unknown>
  argumentNames: string[]
  flagContext: string
  onCommit: (next: Record<string, unknown>) => void
  inputStyle: React.CSSProperties
}) {
  const argsJson = JSON.stringify(args)
  const [rows, setRows] = useState<ArgRow[]>(() => rowsFromArguments(args))
  const lastSynced = useRef(argsJson)
  useEffect(() => {
    // Re-derive rows only on external config changes (node switch, undo);
    // our own commits update lastSynced first so local edit state (row
    // order, in-progress names) survives the round trip.
    if (argsJson !== lastSynced.current) {
      lastSynced.current = argsJson
      setRows(rowsFromArguments(JSON.parse(argsJson) as Record<string, unknown>))
    }
  }, [argsJson])
  const datalistId = useId()

  const commit = (nextRows: ArgRow[]) => {
    setRows(nextRows)
    const next: Record<string, unknown> = {}
    for (const row of nextRows) {
      const name = row.name.trim()
      if (!name) continue
      try {
        next[name] = JSON.parse(row.valueText)
      } catch {
        // Invalid JSON mid-edit: keep the previously persisted value (if
        // any) rather than dropping or corrupting the key.
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
          onClick={() => commit([...rows, { id: nextArgRowId++, name: "", valueText: "" }])}
          className="flex items-center gap-1 text-[11px] font-medium"
          style={{ color: "var(--accent)" }}
        >
          <Plus size={11} /> Add argument
        </button>
      </div>
      <datalist id={datalistId}>
        {argumentNames.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      {rows.length === 0 ? (
        <div className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
          No arguments — polars defaults apply.
        </div>
      ) : (
        <div className="mt-1 space-y-1.5">
          {rows.map((row, i) => {
            const name = row.name.trim()
            const nameUnknown = name !== "" && argumentNames.length > 0 && !argumentNames.includes(name)
            const valueInvalid = row.valueText !== "" && !parsesAsJson(row.valueText)
            return (
              <div key={row.id}>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    list={datalistId}
                    value={row.name}
                    placeholder="name"
                    aria-label={`Argument ${i + 1} name`}
                    onChange={(e) => commit(rows.map((r) => (r.id === row.id ? { ...r, name: e.target.value } : r)))}
                    className="focus-ring w-2/5 px-2 py-1.5 text-xs font-mono rounded-lg"
                    style={inputStyle}
                  />
                  <input
                    type="text"
                    value={row.valueText}
                    placeholder='JSON value, e.g. "," or true'
                    aria-label={`Argument ${i + 1} value`}
                    onChange={(e) => commit(rows.map((r) => (r.id === row.id ? { ...r, valueText: e.target.value } : r)))}
                    className="focus-ring flex-1 px-2 py-1.5 text-xs font-mono rounded-lg"
                    style={inputStyle}
                  />
                  <button
                    type="button"
                    aria-label={`Remove argument ${i + 1}`}
                    onClick={() => commit(rows.filter((r) => r.id !== row.id))}
                    className="icon-danger-btn p-1 rounded shrink-0"
                  >
                    <X size={12} />
                  </button>
                </div>
                {nameUnknown && (
                  <div className="mt-0.5 text-[11px]" style={{ color: "var(--warning-strong)" }}>
                    {name} is not a recognised {flagContext} argument — saved anyway; execution fails loudly.
                  </div>
                )}
                {valueInvalid && (
                  <div className="mt-0.5 text-[11px]" style={{ color: "var(--danger-text)" }}>
                    Invalid JSON value — the previous value is kept until this parses.
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── Source / target fields ──────────────────────────────────────

function PathField({
  config,
  onUpdate,
  extensions,
  inputStyle,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  extensions: string[]
  inputStyle: React.CSSProperties
}) {
  const configPath = configField(config, "path", "")
  const [localPath, setLocalPath] = useState(configPath)
  useEffect(() => { setLocalPath(configPath) }, [configPath])
  const [browsing, setBrowsing] = useState(false)
  const id = useId()
  return (
    <div>
      <div className="flex items-center justify-between">
        <EditorLabel htmlFor={id}>Path</EditorLabel>
        <button
          type="button"
          onClick={() => setBrowsing(!browsing)}
          className="text-[11px] font-medium"
          style={{ color: "var(--accent)" }}
        >
          {browsing ? "close" : "browse"}
        </button>
      </div>
      <input
        id={id}
        type="text"
        value={localPath}
        onChange={(e) => {
          setLocalPath(e.target.value)
          onUpdate("path", e.target.value)
        }}
        className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
        style={inputStyle}
      />
      {browsing && (
        <div className="mt-2">
          <FileBrowser
            currentPath={configPath || undefined}
            onSelect={(p) => {
              onUpdate("path", p)
              setBrowsing(false)
            }}
            extensions={extensions.length > 0 ? extensions.join(",") : undefined}
          />
        </div>
      )}
    </div>
  )
}

function DatabaseFields({
  config,
  onUpdate,
  targetKey,
  targetLabel,
  multiline,
  inputStyle,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  targetKey: "query" | "table"
  targetLabel: string
  multiline: boolean
  inputStyle: React.CSSProperties
}) {
  const configUri = configField(config, "uri", "")
  const [localUri, setLocalUri] = useState(configUri)
  useEffect(() => { setLocalUri(configUri) }, [configUri])
  const configTarget = configField(config, targetKey, "")
  const [localTarget, setLocalTarget] = useState(configTarget)
  useEffect(() => { setLocalTarget(configTarget) }, [configTarget])
  const uriId = useId()
  const targetId = useId()
  return (
    <>
      <div>
        <EditorLabel htmlFor={uriId}>Connection URI</EditorLabel>
        <input
          id={uriId}
          type="text"
          value={localUri}
          onChange={(e) => {
            setLocalUri(e.target.value)
            onUpdate("uri", e.target.value)
          }}
          className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
          style={inputStyle}
        />
      </div>
      <div>
        <EditorLabel htmlFor={targetId}>{targetLabel}</EditorLabel>
        {multiline ? (
          <textarea
            id={targetId}
            value={localTarget}
            rows={3}
            onChange={(e) => {
              setLocalTarget(e.target.value)
              onUpdate(targetKey, e.target.value)
            }}
            className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg resize-y"
            style={inputStyle}
          />
        ) : (
          <input
            id={targetId}
            type="text"
            value={localTarget}
            onChange={(e) => {
              setLocalTarget(e.target.value)
              onUpdate(targetKey, e.target.value)
            }}
            className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
            style={inputStyle}
          />
        )}
      </div>
    </>
  )
}

function RecordsField({
  config,
  onUpdate,
  inputStyle,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputStyle: React.CSSProperties
}) {
  const raw = config.records
  const configText = raw === undefined ? "" : JSON.stringify(raw, null, 2)
  const [text, setText] = useState(configText)
  const [invalid, setInvalid] = useState<string | null>(null)
  const lastCommitted = useRef(configText)
  useEffect(() => {
    if (configText !== lastCommitted.current) {
      lastCommitted.current = configText
      // eslint-disable-next-line react-hooks/set-state-in-effect -- external-config resync: replace draft text with the persisted records
      setText(configText)
      setInvalid(null)
    }
  }, [configText])
  const id = useId()
  const handleChange = (value: string) => {
    setText(value)
    let parsed: unknown
    try {
      parsed = JSON.parse(value)
    } catch {
      setInvalid("Invalid JSON — changes not saved yet")
      return
    }
    if (!Array.isArray(parsed)) {
      setInvalid("Must be a JSON array of records")
      return
    }
    setInvalid(null)
    lastCommitted.current = JSON.stringify(parsed, null, 2)
    onUpdate("records", parsed)
  }
  return (
    <div>
      <EditorLabel htmlFor={id}>Records</EditorLabel>
      <textarea
        id={id}
        value={text}
        rows={6}
        placeholder='[{"a": 1, "b": "x"}]'
        onChange={(e) => handleChange(e.target.value)}
        className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg resize-y"
        style={inputStyle}
      />
      {invalid && (
        <div className="mt-1 text-[11px]" style={{ color: "var(--danger-text)" }}>
          {invalid}
        </div>
      )}
    </div>
  )
}

// ─── Unrecognised keys (1:1 JSON↔UI invariant) ───────────────────

function UnrecognisedKeysSection({
  config,
  keys,
}: {
  config: Record<string, unknown>
  keys: string[]
}) {
  if (keys.length === 0) return null
  return (
    <div
      data-testid="unrecognised-keys"
      className="rounded-lg px-2.5 py-2"
      style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)" }}
    >
      <EditorLabel as="div" color="var(--warning-strong)">Unrecognised keys</EditorLabel>
      <div className="mt-0.5 text-[11px]" style={{ color: "var(--text-secondary)" }}>
        Not edited here — kept as-is in the saved config.
      </div>
      <div className="mt-1 space-y-0.5">
        {keys.map((k) => (
          <div key={k} className="text-xs font-mono break-all" style={{ color: "var(--text-secondary)" }}>
            <span style={{ color: "var(--warning-strong)" }}>{k}</span>: {JSON.stringify(config[k])}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Editor body ──────────────────────────────────────────────────

export default function IoFormatEditor({
  side,
  config,
  onUpdate,
  accentColor,
}: {
  side: IoSide
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  accentColor: string
}) {
  const spec = IO_SIDE_SPECS[side]
  const { formats, error: formatsError } = useIoFormats()
  const format = configField(config, "format", "")
  const capability = formats?.find((f) => f.name === format) ?? null
  const options = (formats ?? []).filter((f) => f[spec.availableKey])
  const modes = capability ? capability[spec.modesKey] : []
  const explicitMode = configField(config, "mode", "")
  const effectiveMode = explicitMode || modes[0] || ""
  const enginesMissing = capability ? capability[spec.enginesMissingKey] : []
  const argumentNames = capability ? capability[spec.argumentsKey][effectiveMode] ?? [] : []

  const argsValue = config.arguments
  const argsIsRecord = argsValue === undefined || isPlainRecord(argsValue)
  const args = isPlainRecord(argsValue) ? argsValue : {}

  // Mode selector: shown when there is a real choice, or when a persisted
  // mode must surface. The default (first listed mode) is displayed but
  // only persisted when the user picks explicitly.
  const modeSelectVisible = modes.length > 1 || Boolean(explicitMode)

  // 1:1 JSON↔UI invariant — anything not rendered by the structured
  // controls above lands in the read-only unrecognised-keys section.
  const renderedKeys = new Set<string>(["format"])
  if (modeSelectVisible) renderedKeys.add("mode")
  if (argsIsRecord) renderedKeys.add("arguments")
  if (capability?.source_kind === "path") renderedKeys.add("path")
  if (capability?.source_kind === "database") {
    renderedKeys.add("uri")
    renderedKeys.add(spec.databaseTargetKey)
  }
  if (capability?.source_kind === "inline") renderedKeys.add("records")
  const unrecognisedKeys = Object.keys(config).filter(
    (k) => !renderedKeys.has(k) && config[k] !== undefined,
  )

  const inputStyle: React.CSSProperties = {
    ...INPUT_STYLE,
    ["--focus-ring-border" as string]: withAlpha(accentColor, 0.3),
    ["--focus-ring-shadow" as string]: withAlpha(accentColor, 0.1),
  }
  const formatId = useId()
  const modeId = useId()

  return (
    <div className="px-4 py-3 space-y-3">
      <div>
        <EditorLabel htmlFor={formatId}>Format</EditorLabel>
        {formatsError ? (
          <div
            className="mt-1 px-2.5 py-2 rounded-lg text-xs"
            style={{ background: "var(--danger-soft)", border: "1px solid var(--danger-border)", color: "var(--danger-text)" }}
          >
            Could not load format capabilities: {formatsError}
          </div>
        ) : formats === null ? (
          <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
            Loading formats...
          </div>
        ) : (
          <>
            <select
              id={formatId}
              value={format}
              onChange={(e) => onUpdate("format", e.target.value)}
              className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs rounded-lg"
              style={inputStyle}
            >
              <option value="">Select a format...</option>
              {format !== "" && !options.some((f) => f.name === format) && (
                <option value={format}>{format} (not available)</option>
              )}
              {options.map((f) => {
                const missing = f[spec.enginesMissingKey]
                return (
                  <option key={f.name} value={f.name}>
                    {f.label}
                    {f.unstable ? " (unstable)" : ""}
                    {missing.length > 0 ? ` — needs one of: ${missing.join(", ")}` : ""}
                  </option>
                )
              })}
            </select>
            {capability?.unstable && (
              <div className="mt-1 text-[11px]" style={{ color: "var(--warning-strong)" }}>
                Unstable: polars marks this format's {side === "input" ? "reader" : "writer"} as unstable.
              </div>
            )}
            {enginesMissing.length > 0 && (
              <div
                className="mt-1 px-2.5 py-1.5 rounded-lg text-[11px]"
                style={{ background: "var(--warning-soft)", border: "1px solid var(--warning-border)", color: "var(--warning-strong)" }}
              >
                Missing engine package — needs one of: {enginesMissing.join(", ")}
              </div>
            )}
          </>
        )}
      </div>

      {modeSelectVisible && (
        <div>
          <EditorLabel htmlFor={modeId}>Mode</EditorLabel>
          <select
            id={modeId}
            value={effectiveMode}
            onChange={(e) => onUpdate("mode", e.target.value)}
            className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs rounded-lg"
            style={inputStyle}
          >
            {explicitMode !== "" && !modes.includes(explicitMode) && (
              <option value={explicitMode}>
                {explicitMode}
                {capability ? " (not valid for this format)" : ""}
              </option>
            )}
            {modes.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      )}

      {capability?.source_kind === "path" && (
        <PathField config={config} onUpdate={onUpdate} extensions={capability.extensions} inputStyle={inputStyle} />
      )}
      {capability?.source_kind === "database" && (
        <DatabaseFields
          config={config}
          onUpdate={onUpdate}
          targetKey={spec.databaseTargetKey}
          targetLabel={spec.databaseTargetLabel}
          multiline={spec.databaseTargetKey === "query"}
          inputStyle={inputStyle}
        />
      )}
      {capability?.source_kind === "inline" && (
        <RecordsField config={config} onUpdate={onUpdate} inputStyle={inputStyle} />
      )}
      {!capability && format === "" && formats !== null && !formatsError && (
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>
          Select a format to configure its {side === "input" ? "source" : "target"}.
        </div>
      )}

      {argsIsRecord && (
        <ArgumentsEditor
          args={args}
          argumentNames={argumentNames}
          flagContext={capability ? `${capability.label} ${effectiveMode}`.trim() : "polars"}
          onCommit={(next) => onUpdate("arguments", next)}
          inputStyle={inputStyle}
        />
      )}

      <UnrecognisedKeysSection config={config} keys={unrecognisedKeys} />
    </div>
  )
}
