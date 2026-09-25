import { CommittedTextArea, EditorLabel } from "../../components/form"
import type { IoCapabilityGroup, IoFormatCapability } from "../../api/types"
import { WarehousePicker, CatalogTablePicker } from "./_DatabricksSelector"
import IoFormatEditor, { IoArgumentsEditor } from "./_IoFormatEditor"
import { hasNonEmptyString, ioBranchConfig } from "./_ioProvider"
import IoProviderPicker from "./_IoProviderPicker"
import { useIoCapabilities } from "./_ioFormats"
import { INPUT_STYLE, SchemaPreview } from "./_shared"
import type { OnReplaceConfig, OnUpdateConfig } from "./_shared"
import { useSchemaFetch } from "../../hooks/useSchemaFetch"

const INPUT_COMMON_KEYS = [
  "instanceOf",
  "inputMapping",
  "selected_columns",
  "column_renames",
  "categorical_levels",
  "contract",
  "code",
  // Post-load steps survive a provider or format change exactly as code does.
  "steps",
] as const

// Editor state the step machinery writes beside `steps`; never persisted, so
// never retained across a provider change, but never a configuration error.
const STEP_EDITOR_STATE_KEYS = ["_steps_error", "_steps_discarded"] as const

const DATABRICKS_KEYS = new Set([
  ...INPUT_COMMON_KEYS,
  ...STEP_EDITOR_STATE_KEYS,
  "inputType",
  "http_path",
  "table",
  "query",
  "arguments",
])

function inputBranchConfig(
  config: Record<string, unknown>,
  group: IoCapabilityGroup,
  requestedFormat?: IoFormatCapability,
  preserveProviderFields = false,
): Record<string, unknown> {
  return ioBranchConfig({
    direction: "input",
    config,
    group,
    commonKeys: INPUT_COMMON_KEYS,
    requestedFormat,
    preserveProviderFields,
  })
}

function databricksConfigurationErrors(config: Record<string, unknown>): string[] {
  const errors: string[] = []
  const inactive = Object.keys(config).filter(
    (key) => config[key] !== undefined && !DATABRICKS_KEYS.has(key),
  )
  if (inactive.length > 0) {
    errors.push(`Unexpected configuration keys: ${inactive.join(", ")}.`)
  }
  if (
    config.arguments !== undefined &&
    (typeof config.arguments !== "object" ||
      config.arguments === null ||
      Array.isArray(config.arguments))
  ) {
    errors.push("Arguments must be an object.")
  } else if (
    config.arguments !== undefined &&
    typeof config.arguments === "object" &&
    config.arguments !== null &&
    !Array.isArray(config.arguments)
  ) {
    const argumentsRecord = config.arguments as Record<string, unknown>
    const unknownArguments = Object.keys(argumentsRecord).filter(
      (name) => name !== "batch_size",
    )
    if (unknownArguments.length > 0) {
      errors.push(
        `Unsupported Databricks arguments: ${unknownArguments.join(", ")}.`,
      )
    }
    const batchSize = argumentsRecord.batch_size
    if (
      batchSize !== undefined &&
      (typeof batchSize !== "number" ||
        !Number.isInteger(batchSize) ||
        batchSize <= 0)
    ) {
      errors.push("Databricks batch_size must be a positive integer.")
    }
  }
  if (config.query !== undefined && !hasNonEmptyString(config.query)) {
    errors.push("The optional SELECT clause must be non-empty when present.")
  }
  return errors
}

export default function DataInputEditor({
  config,
  onUpdate,
  onReplaceConfig,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig: OnReplaceConfig
  accentColor: string
  errorLine?: number | null
}) {
  const { capabilities, error } = useIoCapabilities()
  const groups = (capabilities?.groups ?? []).filter((group) => group.input_available)
  const group = groups.find((candidate) => candidate.name === config.inputType)
  const format = group?.formats.find((candidate) => candidate.name === config.format)
  const databricksErrors =
    group?.name === "databricks" ? databricksConfigurationErrors(config) : []
  const schemaRequired =
    group?.name === "file" && format?.input?.needs_schema_when_bounded === true
  const configuredPath = typeof config.path === "string" ? config.path.trim() : ""
  const {
    schema,
    loading: schemaLoading,
    error: schemaError,
    fetchForPath: fetchSchemaForPath,
  } = useSchemaFetch(
    schemaRequired && configuredPath
      ? configuredPath
      : undefined,
  )
  const configuredArguments =
    typeof config.arguments === "object" && config.arguments !== null && !Array.isArray(config.arguments)
      ? config.arguments as Record<string, unknown>
      : {}
  const hasSchemaMapping =
    typeof configuredArguments.schema === "object" &&
    configuredArguments.schema !== null &&
    !Array.isArray(configuredArguments.schema)

  return (
    <div className="px-4 py-3 space-y-3">
      <IoProviderPicker
        direction="input"
        config={config}
        groups={groups}
        group={group}
        capabilitiesLoaded={capabilities !== null}
        error={error}
        accentColor={accentColor}
        onSelect={(next) => onReplaceConfig(inputBranchConfig(config, next))}
      />

      {group?.name === "databricks" ? (
        <div className="space-y-3">
          {databricksErrors.length > 0 && (
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
              {databricksErrors.map((message) => (
                <p key={message}>{message}</p>
              ))}
            </section>
          )}
          <WarehousePicker
            httpPath={typeof config.http_path === "string" ? config.http_path : ""}
            onSelect={(value) => onUpdate("http_path", value)}
          />
          <CatalogTablePicker
            table={typeof config.table === "string" ? config.table : ""}
            onSelect={(value) => onUpdate("table", value)}
          />
          <div>
            <EditorLabel>SELECT clause</EditorLabel>
            <CommittedTextArea
              aria-label="SELECT clause"
              value={typeof config.query === "string" ? config.query : ""}
              onCommit={(value) => {
                const query = value.trim()
                if (query) {
                  onUpdate("query", query)
                } else if ("query" in config) {
                  const next = { ...config }
                  delete next.query
                  onReplaceConfig(next)
                }
              }}
              rows={3}
              className="mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
              style={INPUT_STYLE}
            />
            <p className="mt-1 text-[11px]" style={{ color: "var(--text-muted)" }}>
              Optional projection/filter clause. Haute supplies the validated table.
            </p>
          </div>
          <IoArgumentsEditor
            value={config.arguments}
            argumentNames={["batch_size"]}
            context="Databricks snapshot"
            onCommit={(next) => onUpdate("arguments", next)}
          />
        </div>
      ) : group ? (
        <IoFormatEditor
          group={group}
          direction="input"
          config={config}
          onUpdate={onUpdate}
          onSelectFormat={(next) =>
            onReplaceConfig(inputBranchConfig(config, group, next, true))
          }
          accentColor={accentColor}
        />
      ) : null}

      {schemaRequired && configuredPath && (
        <section aria-label="Detected schema" className="space-y-2">
          {schemaLoading && (
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              Loading schema...
            </p>
          )}
          {schemaError && (
            <div className="space-y-1">
              <p role="alert" className="text-xs" style={{ color: "var(--danger-text)" }}>
                Could not detect schema: {schemaError}
              </p>
              <button
                type="button"
                onClick={() => fetchSchemaForPath(configuredPath)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium"
                style={{ background: "var(--bg-elevated)", color: "var(--text-primary)" }}
              >
                Retry schema
              </button>
            </div>
          )}
          {!hasSchemaMapping && (
            <p className="text-xs" style={{ color: "var(--danger-text)" }}>
              A schema mapping is required for this bounded input.
            </p>
          )}
          <SchemaPreview schema={schema} />
          {schema && (
            <button
              type="button"
              onClick={() =>
                onUpdate("arguments", {
                  ...configuredArguments,
                  schema: Object.fromEntries(
                    schema.columns.map((column) => [column.name, column.dtype]),
                  ),
                })
              }
              className="px-3 py-1.5 rounded-lg text-xs font-medium"
              style={{ background: "var(--accent)", color: "white" }}
            >
              Use detected schema
            </button>
          )}
        </section>
      )}

    </div>
  )
}
