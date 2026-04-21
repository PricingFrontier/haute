import { useState, useEffect, useRef, useMemo, useCallback } from "react"
import { useReactFlow, useNodes } from "@xyflow/react"
import { Search } from "lucide-react"
import { NODE_TYPE_META, type NodeTypeValue } from "../utils/nodeTypes"
import { nodeData } from "../types/node"

interface NodeSearchProps {
  onClose: () => void
  onSelectNode: (nodeId: string) => void
}

export const NODE_SEARCH_RESULT_ROW_HEIGHT = 40
export const NODE_SEARCH_VISIBLE_ROWS = 14
export const NODE_SEARCH_OVERSCAN_ROWS = 6

type NodeSearchResult = {
  id: string
  label: string
  meta: typeof NODE_TYPE_META[NodeTypeValue]
  x: number
  y: number
  normalizedLabel: string
  normalizedTypeName: string
  normalizedTypeLabel: string
}

function visibleRowCount(list: HTMLDivElement): number {
  const measuredRows = Math.floor(list.clientHeight / NODE_SEARCH_RESULT_ROW_HEIGHT)
  return measuredRows > 0 ? measuredRows : NODE_SEARCH_VISIBLE_ROWS
}

/** Command-palette style node search. Only mounted when open (parent controls lifecycle). */
export default function NodeSearch({ onClose, onSelectNode }: NodeSearchProps) {
  const [query, setQuery] = useState("")
  const [activeIndex, setActiveIndex] = useState(0)
  const [windowStart, setWindowStart] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const { setCenter } = useReactFlow()
  const allNodes = useNodes()

  const indexedNodes = useMemo<NodeSearchResult[]>(() => allNodes.map((n) => {
    const d = nodeData(n)
    const meta = NODE_TYPE_META[d.nodeType as NodeTypeValue]
    const label = (d.label as string) || ""
    const typeName = meta?.name || ""
    const typeLabel = meta?.label || ""
    return {
      id: n.id,
      label,
      meta,
      x: n.position.x,
      y: n.position.y,
      normalizedLabel: label.toLowerCase(),
      normalizedTypeName: typeName.toLowerCase(),
      normalizedTypeLabel: typeLabel.toLowerCase(),
    }
  }), [allNodes])

  const results = useMemo(() => {
    if (!query.trim()) return indexedNodes
    const q = query.toLowerCase()
    return indexedNodes.filter((item) =>
      item.normalizedLabel.includes(q) ||
      item.normalizedTypeName.includes(q) ||
      item.normalizedTypeLabel.includes(q)
    )
  }, [query, indexedNodes])

  const maxRenderedRows = NODE_SEARCH_VISIBLE_ROWS + NODE_SEARCH_OVERSCAN_ROWS * 2
  const maxWindowStart = Math.max(0, results.length - maxRenderedRows)
  const visibleStart = Math.max(0, Math.min(windowStart, maxWindowStart))
  const visibleEnd = Math.min(results.length, visibleStart + maxRenderedRows)
  const visibleResults = results.slice(visibleStart, visibleEnd)
  const activeResultIndex = results.length === 0 ? 0 : Math.min(activeIndex, results.length - 1)
  const activeResult = results[activeResultIndex]
  const activeResultId = activeResult?.id
  const activeResultIsRendered =
    activeResult != null && activeResultIndex >= visibleStart && activeResultIndex < visibleEnd

  // Focus input on mount
  useEffect(() => {
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [])

  const scrollIndexIntoView = useCallback((index: number) => {
    const list = listRef.current
    if (!list) {
      setWindowStart(Math.max(0, index - NODE_SEARCH_OVERSCAN_ROWS))
      return
    }

    const rowsInViewport = visibleRowCount(list)
    const firstVisible = Math.floor(list.scrollTop / NODE_SEARCH_RESULT_ROW_HEIGHT)
    const lastVisible = firstVisible + rowsInViewport - 1
    let nextScrollTop = list.scrollTop

    if (index < firstVisible) {
      nextScrollTop = index * NODE_SEARCH_RESULT_ROW_HEIGHT
    } else if (index > lastVisible) {
      nextScrollTop = Math.max(0, index - rowsInViewport + 1) * NODE_SEARCH_RESULT_ROW_HEIGHT
    }

    list.scrollTop = nextScrollTop
  }, [])

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setWindowStart(Math.max(0, Math.floor(e.currentTarget.scrollTop / NODE_SEARCH_RESULT_ROW_HEIGHT) - NODE_SEARCH_OVERSCAN_ROWS))
  }, [])

  const selectResult = useCallback((index: number) => {
    const item = results[index]
    if (!item) return
    setCenter(item.x + 100, item.y + 25, { zoom: 0.8, duration: 300 })
    onSelectNode(item.id)
    onClose()
  }, [results, setCenter, onSelectNode, onClose])

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value)
    setActiveIndex(0)
    setWindowStart(0)
    if (listRef.current) listRef.current.scrollTop = 0
  }, [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault()
      if (results.length === 0) return
      const nextIndex = Math.min(activeResultIndex + 1, results.length - 1)
      setActiveIndex(nextIndex)
      scrollIndexIntoView(nextIndex)
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      if (results.length === 0) return
      const nextIndex = Math.max(activeResultIndex - 1, 0)
      setActiveIndex(nextIndex)
      scrollIndexIntoView(nextIndex)
    } else if (e.key === "Enter") {
      e.preventDefault()
      selectResult(activeResultIndex)
    } else if (e.key === "Escape") {
      e.preventDefault()
      onClose()
    }
  }, [activeResultIndex, onClose, results.length, scrollIndexIntoView, selectResult])

  return (
    <div
      data-testid="node-search-dialog"
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
            role="combobox"
            aria-label="Search nodes"
            aria-autocomplete="list"
            aria-expanded="true"
            type="text"
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
            onKeyDown={handleKeyDown}
            aria-controls="node-search-results"
            aria-activedescendant={activeResultId ? `node-search-result-${activeResultId}` : undefined}
            placeholder="Search nodes by name or type..."
            className="flex-1 bg-transparent text-[14px] focus:outline-none"
            style={{ color: "var(--text-primary)" }}
          />
          <kbd className="text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: "var(--bg-hover)", color: "var(--text-muted)" }}>
            Esc
          </kbd>
        </div>

        {/* Results list */}
        <div
          ref={listRef}
          id="node-search-results"
          role="listbox"
          aria-label="Node results"
          className="max-h-[85vh] overflow-y-auto py-1"
          style={results.length > 0 ? { height: Math.min(results.length, NODE_SEARCH_VISIBLE_ROWS) * NODE_SEARCH_RESULT_ROW_HEIGHT } : undefined}
          onScroll={handleScroll}
        >
          {results.length === 0 ? (
            <div className="px-4 py-6 text-center text-[13px]" style={{ color: "var(--text-muted)" }}>
              No matching nodes
            </div>
          ) : (
            <div className="relative" style={{ height: results.length * NODE_SEARCH_RESULT_ROW_HEIGHT }}>
              {activeResult && !activeResultIsRendered && (
                <div
                  id={`node-search-result-${activeResult.id}`}
                  role="option"
                  aria-selected="true"
                  aria-posinset={activeResultIndex + 1}
                  aria-setsize={results.length}
                  aria-label={`${activeResult.meta?.name || "Node"}: ${activeResult.label}`}
                  style={{
                    position: "absolute",
                    width: 1,
                    height: 1,
                    overflow: "hidden",
                    clipPath: "inset(50%)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {activeResult.label}
                </div>
              )}
              {visibleResults.map((item, offset) => {
                const i = visibleStart + offset
                const Icon = item.meta?.icon
                const accent = item.meta?.color || "var(--text-secondary)"
                return (
                  <button
                    key={item.id}
                    id={`node-search-result-${item.id}`}
                    role="option"
                    aria-selected={i === activeResultIndex}
                    aria-posinset={i + 1}
                    aria-setsize={results.length}
                    aria-label={`${item.meta?.name || "Node"}: ${item.label}`}
                    className="absolute left-0 right-0 w-full flex items-center gap-3 px-4 text-left transition-colors"
                    style={{
                      top: i * NODE_SEARCH_RESULT_ROW_HEIGHT,
                      height: NODE_SEARCH_RESULT_ROW_HEIGHT,
                      background: i === activeResultIndex ? "var(--bg-hover)" : "transparent",
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
                    <span className="text-[13px] truncate min-w-0 flex-1" style={{ color: "var(--text-primary)" }} title={item.label}>
                      {item.label}
                    </span>
                  </button>
                )
              })}
            </div>
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
