import { InputSourcesBar } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import PathPickerField from "./shared/PathPickerField"

/** The extensions Load File's loaders read: pickle, JSON and joblib. */
export const LOAD_FILE_EXTENSIONS = ".pkl,.pickle,.json,.joblib"

export default function ExternalFileEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  accentColor: string
}) {
  const fileType = configField(config, "fileType", "pickle")

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-2">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>File Type</label>
        <div className="mt-1">
          <ToggleButtonGroup
            value={fileType}
            onChange={(ft) => onUpdate("fileType", ft)}
            options={[
              { key: "pickle", label: "PICKLE" },
              { key: "json", label: "JSON" },
              { key: "joblib", label: "JOBLIB" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      <PathPickerField
        label="File Path"
        value={configField(config, "path", "")}
        onSelect={(path) => onUpdate("path", path)}
        extensions={LOAD_FILE_EXTENSIONS}
      />

    </div>
  )
}
