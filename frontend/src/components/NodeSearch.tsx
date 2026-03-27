import { useState, useEffect, useRef, useMemo, useCallback } from "react"
import { useReactFlow, useNodes } from "@xyflow/react"
import { Search } from "lucide-react"
import { NODE_TYPE_META, type NodeTypeValue } from "../utils/nodeTypes"
import { nodeData } from "../types/node"

interface NodeSearchProps {
  onClose: () => void
  onSelectNode: (nodeId: string) => void
}

/** Command-palette style node search. Only mounted when open (parent controls lifecycle). */
export default function NodeSearch({ onClose, onSelectNode }: NodeSearchProps) {
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const { setCenter } = useReactFlow()
  const allNodes = useNodes()

  const results = useMemo(() => {
    const nodes = allNodes
    if (!query.trim()) {
      return nodes.map((n) => {
        const d = nodeData(n)
        const meta = NODE_TYPE_META[d.nodeType as NodeTypeValue]
        return { id: n.id, label: d.label as string, nodeType: d.nodeType as string, meta, x: n.position.x, y: n.position.y }
      })
    }
    const q = query.toLowerCase()
    return nodes
      .map((n) => {
        const d = nodeData(n)
        const meta = NODE_TYPE_META[d.nodeType as NodeTypeValue]
        const label = (d.label as string) || ""
        const typeName = meta?.name || ""
        const typeLabel = meta?.label || ""
        if (
          label.toLowerCase().includes(q) ||
          typeName.toLowerCase().includes(q) ||
          typeLabel.toLowerCase().includes(q)
        ) {
          return { id: n.id, label, nodeType: d.nodeType as string, meta, x: n.position.x, y: n.position.y }
        }
        return null
      })
      .filter(Boolean) as { id: string; label: string; nodeType: string; meta: typeof NODE_TYPE_META[NodeTypeValue]; x: number; y: number }[]
  }, [query, allNodes])

  // Reset active index when results change
  // eslint-disable-next-line react-hooks/set-state-in-effect -- derived state reset: active index must restart when filtered results change
  useEffect(() => { setActiveIndex(0) }, [results.length])

  // Focus input on mount
  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [])

  // Scroll active item into view
  useEffect(() => {
    if (!listRef.current) return
    const active = listRef.current.children[activeIndex] as HTMLElement | undefined
    active?.scrollIntoView({ block: "nearest" })
  }, [activeIndex])

  const selectResult = useCallback((index: number) => {
    const item = results[index]
    if (!item) return
    setCenter(item.x + 100, item.y + 25, { zoom: 0.8, duration: 300 })
    onSelectNode(item.id)
    onClose()
  }, [results, setCenter, onSelectNode, onClose])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, results.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === "Enter") {
      e.preventDefault()
      selectResult(activeIndex)
    } else if (e.key === "Escape") {
      e.preventDefault()
      onClose()
    }
  }, [results.length, activeIndex, selectResult, onClose])

  return (
    <div
      className="fixed inset-0 z-[100] flex justify-center pt-[3vh]"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" />

      {/* Search panel */}
      <div
        role="dialog"
        aria-label="Search pipeline nodes"
        className="relative w-full max-w-md rounded-xl shadow-2xl overflow-hidden animate-fade-in"
        style={{ background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }}
      >
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
          <Search size={16} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search nodes by name or type..."
            className="flex-1 bg-transparent text-[14px] focus:outline-none"
            style={{ color: "var(--text-primary)" }}
          />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: "var(--bg-hover)", color: "var(--text-muted)" }}>
            Esc
          </kbd>
        </div>

        {/* Results list */}
        <div ref={listRef} role="listbox" aria-label="Node results" className="max-h-[85vh] overflow-y-auto py-1">
          {results.length === 0 ? (
            <div className="px-4 py-6 text-center text-[13px]" style={{ color: "var(--text-muted)" }}>
              No matching nodes
            </div>
          ) : (
            results.map((item, i) => {
              const Icon = item.meta?.icon
              const accent = item.meta?.color || "var(--text-secondary)"
              return (
                <button
                  key={item.id}
                  role="option"
                  aria-selected={i === activeIndex}
                  aria-label={`${item.meta?.name || "Node"}: ${item.label}`}
                  className="w-full flex items-center gap-3 px-4 py-2 text-left transition-colors"
                  style={{
                    background: i === activeIndex ? "var(--bg-hover)" : "transparent",
                  }}
                  onMouseEnter={() => setActiveIndex(i)}
                  onClick={() => selectResult(i)}
                >
                  {Icon && <Icon size={14} style={{ color: accent }} className="shrink-0" />}
                  <span
                    className="text-[10px] font-bold uppercase tracking-[0.08em] shrink-0 min-w-[60px]"
                    style={{ color: accent }}
                  >
                    {item.meta?.label || "NODE"}
                  </span>
                  <span className="text-[13px] truncate" style={{ color: "var(--text-primary)" }}>
                    {item.label}
                  </span>
                </button>
              )
            })
          )}
        </div>

        {/* Footer hint */}
        <div className="flex items-center gap-3 px-4 py-2 text-[11px]" style={{ borderTop: "1px solid var(--border)", color: "var(--text-muted)" }}>
          <span><kbd className="font-mono">↑↓</kbd> navigate</span>
          <span><kbd className="font-mono">↵</kbd> jump to node</span>
          <span><kbd className="font-mono">esc</kbd> close</span>
        </div>
      </div>
    </div>
  )
}
