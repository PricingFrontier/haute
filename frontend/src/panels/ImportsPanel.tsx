import { Package } from "lucide-react"
import { CodeEditor } from "./editors"
import PanelShell from "./PanelShell"

interface ImportsPanelProps {
  preamble: string
  onPreambleChange: (value: string) => void
  onClose: () => void
}

export default function ImportsPanel({ preamble, onPreambleChange, onClose }: ImportsPanelProps) {
  return (
    <PanelShell
      title="Pipeline Imports"
      onClose={onClose}
      icon={<Package size={14} style={{ color: 'var(--accent)' }} />}
      subtitle={
        <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Import statements for utility modules and third-party libraries.
        </span>
      }
    >
      {/* Info */}
      <div className="px-3 py-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        <p className="text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>
          <span style={{ color: "var(--text-accent-muted)" }}>import polars as pl</span> and <span style={{ color: "var(--text-accent-muted)" }}>import haute</span> are always included
        </p>
      </div>

      {/* Editor */}
      <div className="flex-1 min-h-0">
        <CodeEditor
          defaultValue={preamble}
          onChange={onPreambleChange}
          placeholder={"from utility.features import *\nimport numpy as np\nfrom catboost import CatBoostRegressor"}
        />
      </div>
    </PanelShell>
  )
}
