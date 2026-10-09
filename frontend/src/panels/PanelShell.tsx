import { useState } from "react"
import { SidePanel, type SidePanelProps } from "../haute-ui"
import useUIStore from "../stores/useUIStore"

const MIN_PANEL_W = 320
const LEFT_PALETTE_W = 180
const LEFT_PALETTE_COLLAPSED_W = 40

/** Available space = window width minus left palette. */
function availableSpace(): number {
  const paletteOpen = useUIStore.getState().paletteOpen
  const leftW = paletteOpen ? LEFT_PALETTE_W : LEFT_PALETTE_COLLAPSED_W
  return window.innerWidth - leftW
}

/** Default panel width: 50% of available space. */
function defaultPanelWidth(): number {
  return Math.max(MIN_PANEL_W, Math.floor(availableSpace() / 2))
}

/** Maximum panel width: 75% of available space so the graph stays usable. */
function maxPanelWidth(): number {
  return Math.max(MIN_PANEL_W, Math.floor(availableSpace() * 0.75))
}

/** Omit over each member of a union, so a header's title still needs its onClose. */
type OmitEach<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never

type PanelShellProps = OmitEach<SidePanelProps, "width" | "onWidthChange" | "minWidth" | "maxWidth"> & {
  /** Optional per-panel width ceiling (px). Sidebar-style panels (Git) cap
   *  here so they don't open half-screen-wide on a large monitor (S38). */
  maxWidth?: number
}

/**
 * Shared wrapper for all right-side panels (NodePanel, UtilityPanel,
 * ImportsPanel, GitPanel, TracePanel): haute-ui's `SidePanel` (the drag
 * handle, slide-in and optional header, shared with extensions), sized from
 * the UI store. All panels share one width. With none stored, a panel takes
 * half the space beside the palette when it mounts, and keeps that width
 * across unrelated rerenders and viewport changes; only a drag changes it.
 */
export default function PanelShell({ maxWidth, ...props }: PanelShellProps) {
  const storedWidth = useUIStore((s) => s.nodePanelWidth)
  const setNodePanelWidth = useUIStore((s) => s.setNodePanelWidth)
  const [mountDefaultWidth] = useState(defaultPanelWidth)
  const rawWidth = storedWidth > 0 ? storedWidth : mountDefaultWidth
  // Per-panel ceiling (e.g. the Git sidebar) clamps the shared width locally.
  const width = maxWidth ? Math.min(rawWidth, maxWidth) : rawWidth

  return (
    <SidePanel
      {...props}
      width={width}
      onWidthChange={setNodePanelWidth}
      minWidth={MIN_PANEL_W}
      maxWidth={() => (maxWidth ? Math.min(maxPanelWidth(), maxWidth) : maxPanelWidth())}
    />
  )
}
