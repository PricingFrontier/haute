import { InputSourcesBar } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import PathPickerField from "./shared/PathPickerField"

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
  const modelClass = configField(config, "modelClass", "classifier")

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
              { key: "catboost", label: "CATBOOST" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      {fileType === "catboost" && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Model Type</label>
          <div className="mt-1">
            <ToggleButtonGroup
              value={modelClass}
              onChange={(mc) => onUpdate("modelClass", mc)}
              options={[
                { key: "classifier", label: "Classifier" },
                { key: "regressor", label: "Regressor" },
              ]}
              accentColor={accentColor}
            />
          </div>
        </div>
      )}

      <PathPickerField
        label="File Path"
        value={configField(config, "path", "")}
        onSelect={(path) => onUpdate("path", path)}
        extensions=".pkl,.pickle,.json,.joblib,.cbm,.onnx,.pmml"
      />

    </div>
  )
}
