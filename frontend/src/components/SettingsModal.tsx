interface SettingsModalProps {
  preamble: string
  onPreambleChange: (value: string) => void
  onClose: () => void
}

export default function SettingsModal({ preamble, onPreambleChange, onClose }: SettingsModalProps) {
  return (
    <div
      data-testid="settings-modal"
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label="Pipeline imports and helpers"
      style={{ background: 'rgba(0,0,0,.5)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="w-[560px] max-h-[80vh] flex flex-col rounded-xl overflow-hidden shadow-2xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}>
        <div className="px-4 py-3 flex items-center justify-between shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Pipeline Imports &amp; Helpers</h2>
            <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Extra imports, constants, and helper functions. Preserved across GUI saves.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close settings"
            className="hover-bg p-1 rounded"
            style={{ color: 'var(--text-muted)' }}
          >
            ✕
          </button>
        </div>
        <div className="flex-1 min-h-0 p-4">
          <div className="text-[11px] font-mono mb-2 px-1" style={{ color: 'var(--text-muted)' }}>
            <span style={{ color: 'var(--text-accent-muted)' }}>import polars as pl</span> and <span style={{ color: 'var(--text-accent-muted)' }}>import haute</span> are always included
          </div>
          <textarea
            defaultValue={preamble}
            onChange={(e) => onPreambleChange(e.target.value)}
            spellCheck={false}
            placeholder={"import numpy as np\nimport catboost\nfrom sklearn.preprocessing import StandardScaler\n\n# Helper functions\ndef my_helper(x):\n    return x * 2"}
            className="focus-ring w-full h-[300px] px-3 py-2.5 text-[12px] font-mono rounded-lg resize-none"
            style={{
              background: 'var(--bg-input)',
              border: '1px solid var(--border)',
              color: 'var(--syntax-text)',
              caretColor: 'var(--accent)',
              lineHeight: '1.625',
            }}
          />
        </div>
        <div className="px-4 py-3 flex justify-end shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-[12px] font-semibold text-white rounded-md transition-colors hover:bg-[var(--accent-hover)]"
            style={{ background: 'var(--accent)' }}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
