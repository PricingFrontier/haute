import { useState, useEffect } from "react"
import { HardDriveDownload } from "lucide-react"
import type { OnUpdateConfig } from "./_shared"
import { executeSink } from "../../api/client"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { buildGraph } from "../../utils/buildGraph"
import useSettingsStore from "../../stores/useSettingsStore"
import { EditorLabel } from "../../components/form"
import { useGraph } from "../useGraph"

export default function SinkEditor({
  config,
  onUpdate,
  nodeId,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeId: string
  accentColor: string
}) {
  const { allNodes, edges, submodels, preamble } = useGraph()
  const format = configField(config, "format", "parquet")
  const [writing, setWriting] = useState(false)
  const [writeResult, setWriteResult] = useState<{ status: string; message: string } | null>(null)
  const configPath = configField(config, "path", "")
  const [localPath, setLocalPath] = useState(configPath)
  useEffect(() => { setLocalPath(configPath) }, [configPath])

  const hasPath = Boolean(config.path)

  const handleWrite = () => {
    if (!hasPath || writing) return
    setWriting(true)
    setWriteResult(null)

    const graph = buildGraph(allNodes, edges, submodels, preamble)

    executeSink(graph, nodeId, useSettingsStore.getState().activeSource)
      .then((data) => {
        setWriteResult({ status: data.status || "ok", message: data.message || "Written successfully" })
        setWriting(false)
      })
      .catch((err: Error) => {
        setWriteResult({ status: "error", message: err.message })
        setWriting(false)
      })
  }

  return (
    <div className="px-4 py-3 space-y-3">
      <div>
        <EditorLabel>Format</EditorLabel>
        <div className="mt-1">
          <ToggleButtonGroup
            value={format}
            onChange={(fmt) => onUpdate("format", fmt)}
            options={[
              { key: "parquet", label: "PARQUET" },
              { key: "csv", label: "CSV" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      <div>
        <EditorLabel className="mb-1.5 block">Output Path</EditorLabel>
        <input
          type="text"
          placeholder=""
          value={localPath}
          onChange={(e) => { setLocalPath(e.target.value); onUpdate("path", e.target.value) }}
          className="focus-ring w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
          style={{
            background: 'var(--bg-input)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            ['--focus-ring-border' as string]: withAlpha(accentColor, 0.3),
            ['--focus-ring-shadow' as string]: withAlpha(accentColor, 0.1),
          }}
        />
      </div>

      <button
        onClick={handleWrite}
        disabled={!hasPath || writing}
        className="w-full flex items-center justify-center gap-2 px-3 py-2 text-[12px] font-semibold rounded-lg transition-opacity disabled:opacity-40 enabled:hover:opacity-85"
        style={{ background: accentColor, color: '#000' }}
      >
        <HardDriveDownload size={14} />
        {writing ? "Writing..." : "Write"}
      </button>

      {writeResult && (
        <div
          className="px-2.5 py-2 rounded-lg text-xs"
          style={{
            background: writeResult.status === "ok" ? 'var(--success-soft)' : 'var(--danger-soft)',
            border: writeResult.status === "ok" ? '1px solid var(--success-border)' : '1px solid var(--danger-border)',
            color: writeResult.status === "ok" ? 'var(--success-hover)' : 'var(--danger-text)',
          }}
        >
          {writeResult.message}
        </div>
      )}
    </div>
  )
}
