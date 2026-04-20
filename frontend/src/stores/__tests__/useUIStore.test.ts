import { describe, it, expect, beforeEach } from "vitest"
import useUIStore from "../useUIStore"

function reset() {
  useUIStore.setState({
    paletteOpen: true,
    utilityOpen: false,
    importsOpen: false,
    gitOpen: false,
    shortcutsOpen: false,
    submodelDialog: null,
    renameDialog: null,
    syncBanner: null,
    nodePanelWidth: 0,
    hoveredNodeId: null,
    nodeSearchOpen: false,
  })
}

describe("useUIStore", () => {
  beforeEach(reset)

  // -----------------------------------------------------------------------
  // Modals / panels
  // -----------------------------------------------------------------------

  describe("setPaletteOpen", () => {
    it("toggles palette state", () => {
      expect(useUIStore.getState().paletteOpen).toBe(true)
      useUIStore.getState().setPaletteOpen(false)
      expect(useUIStore.getState().paletteOpen).toBe(false)
    })
  })

  describe("setShortcutsOpen", () => {
    it("accepts a boolean", () => {
      useUIStore.getState().setShortcutsOpen(true)
      expect(useUIStore.getState().shortcutsOpen).toBe(true)
    })

    it("accepts a function updater", () => {
      useUIStore.getState().setShortcutsOpen(false)
      useUIStore.getState().setShortcutsOpen((prev) => !prev)
      expect(useUIStore.getState().shortcutsOpen).toBe(true)
    })
  })

  describe("setSubmodelDialog", () => {
    it("sets and clears dialog", () => {
      useUIStore.getState().setSubmodelDialog({ nodeIds: ["a", "b"] })
      expect(useUIStore.getState().submodelDialog).toEqual({ nodeIds: ["a", "b"] })
      useUIStore.getState().setSubmodelDialog(null)
      expect(useUIStore.getState().submodelDialog).toBeNull()
    })
  })

  // -----------------------------------------------------------------------
  // Sync banner
  // -----------------------------------------------------------------------

  describe("setSyncBanner", () => {
    it("sets and clears banner", () => {
      useUIStore.getState().setSyncBanner("Parse error")
      expect(useUIStore.getState().syncBanner).toBe("Parse error")
      useUIStore.getState().setSyncBanner(null)
      expect(useUIStore.getState().syncBanner).toBeNull()
    })
  })

  // -----------------------------------------------------------------------
  // Last-saved snapshot moved to useGraphStore (Wave 7E consolidation).
  //
  // Shape assertions here pin that useUIStore no longer carries dirty
  // state so a future refactor doesn't silently reintroduce the two-
  // source-of-truth bug (see item #99).  Full behavioural coverage for
  // markSaved / isDirty lives in useGraphStore.consolidation.test.ts.
  // -----------------------------------------------------------------------

  describe("no dirty-tracking state on useUIStore (Wave 7E)", () => {
    it("does not expose lastSavedSnapshot", () => {
      const state = useUIStore.getState() as unknown as Record<string, unknown>
      expect(state).not.toHaveProperty("lastSavedSnapshot")
    })

    it("does not expose markSaved", () => {
      const state = useUIStore.getState() as unknown as Record<string, unknown>
      expect(state).not.toHaveProperty("markSaved")
    })

    it("does not expose the legacy dirty boolean / setDirty", () => {
      const state = useUIStore.getState() as unknown as Record<string, unknown>
      expect(state).not.toHaveProperty("dirty")
      expect(state).not.toHaveProperty("setDirty")
    })
  })

  // -----------------------------------------------------------------------
  // Git panel mutual exclusion
  // -----------------------------------------------------------------------

  describe("setGitOpen", () => {
    it("opens git panel", () => {
      useUIStore.getState().setGitOpen(true)
      expect(useUIStore.getState().gitOpen).toBe(true)
    })

    it("closes utility and imports when opening git", () => {
      useUIStore.getState().setUtilityOpen(true)
      useUIStore.getState().setImportsOpen(true)
      useUIStore.getState().setGitOpen(true)
      expect(useUIStore.getState().gitOpen).toBe(true)
      expect(useUIStore.getState().utilityOpen).toBe(false)
      expect(useUIStore.getState().importsOpen).toBe(false)
    })

    it("setting utility closes git", () => {
      useUIStore.getState().setGitOpen(true)
      useUIStore.getState().setUtilityOpen(true)
      expect(useUIStore.getState().gitOpen).toBe(false)
    })

    it("setting imports closes git", () => {
      useUIStore.getState().setGitOpen(true)
      useUIStore.getState().setImportsOpen(true)
      expect(useUIStore.getState().gitOpen).toBe(false)
    })
  })

  describe("setUtilityOpen mutual exclusion", () => {
    it("closes git and imports when opening utility", () => {
      useUIStore.getState().setGitOpen(true)
      useUIStore.getState().setImportsOpen(true)
      useUIStore.getState().setUtilityOpen(true)
      expect(useUIStore.getState().utilityOpen).toBe(true)
      expect(useUIStore.getState().gitOpen).toBe(false)
      expect(useUIStore.getState().importsOpen).toBe(false)
    })
  })

  describe("setImportsOpen mutual exclusion", () => {
    it("closes git and utility when opening imports", () => {
      useUIStore.getState().setGitOpen(true)
      useUIStore.getState().setUtilityOpen(true)
      useUIStore.getState().setImportsOpen(true)
      expect(useUIStore.getState().importsOpen).toBe(true)
      expect(useUIStore.getState().gitOpen).toBe(false)
      expect(useUIStore.getState().utilityOpen).toBe(false)
    })
  })

  // -----------------------------------------------------------------------
  // Node panel width
  // -----------------------------------------------------------------------

  describe("nodePanelWidth", () => {
    it("defaults to 0 (sentinel for dynamic sizing)", () => {
      expect(useUIStore.getState().nodePanelWidth).toBe(0)
    })

    it("setNodePanelWidth updates the width", () => {
      useUIStore.getState().setNodePanelWidth(600)
      expect(useUIStore.getState().nodePanelWidth).toBe(600)
    })
  })

  // -----------------------------------------------------------------------
  // hoveredNodeId — connected edges glow, unconnected nodes dim
  // Catches: forgetting to clear hoveredNodeId on mouse-leave would leave
  // the canvas permanently dimmed.
  // -----------------------------------------------------------------------

  describe("hoveredNodeId", () => {
    it("defaults to null", () => {
      expect(useUIStore.getState().hoveredNodeId).toBeNull()
    })

    it("setHoveredNodeId sets a node id", () => {
      useUIStore.getState().setHoveredNodeId("node-42")
      expect(useUIStore.getState().hoveredNodeId).toBe("node-42")
    })

    it("setHoveredNodeId(null) clears the hover", () => {
      useUIStore.getState().setHoveredNodeId("node-42")
      useUIStore.getState().setHoveredNodeId(null)
      expect(useUIStore.getState().hoveredNodeId).toBeNull()
    })

    it("overwriting with a different node id replaces the previous one", () => {
      useUIStore.getState().setHoveredNodeId("node-1")
      useUIStore.getState().setHoveredNodeId("node-2")
      expect(useUIStore.getState().hoveredNodeId).toBe("node-2")
    })
  })

  // -----------------------------------------------------------------------
  // nodeSearchOpen (Ctrl+K) — supports both boolean and function updater
  // Catches: if the function updater path were removed, Ctrl+K toggle
  // (setNodeSearchOpen(prev => !prev)) would silently fail.
  // -----------------------------------------------------------------------

  describe("nodeSearchOpen", () => {
    it("defaults to false", () => {
      expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    })

    it("setNodeSearchOpen(true) opens node search", () => {
      useUIStore.getState().setNodeSearchOpen(true)
      expect(useUIStore.getState().nodeSearchOpen).toBe(true)
    })

    it("setNodeSearchOpen accepts a function updater (toggle pattern)", () => {
      useUIStore.getState().setNodeSearchOpen(false)
      useUIStore.getState().setNodeSearchOpen((prev) => !prev)
      expect(useUIStore.getState().nodeSearchOpen).toBe(true)
      useUIStore.getState().setNodeSearchOpen((prev) => !prev)
      expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    })

    it("function updater reads current state, not stale closure value", () => {
      // Simulate rapid toggle: open then immediately toggle via updater
      useUIStore.getState().setNodeSearchOpen(true)
      useUIStore.getState().setNodeSearchOpen((prev) => {
        // prev should be true because we just set it
        expect(prev).toBe(true)
        return false
      })
      expect(useUIStore.getState().nodeSearchOpen).toBe(false)
    })
  })

  // -----------------------------------------------------------------------
  // renameDialog — stores nodeId + currentLabel for the rename modal
  // Catches: if renameDialog shape changes (e.g. dropping currentLabel),
  // the rename modal would render with undefined text.
  // -----------------------------------------------------------------------

  describe("renameDialog", () => {
    it("defaults to null", () => {
      expect(useUIStore.getState().renameDialog).toBeNull()
    })

    it("setRenameDialog opens with nodeId and currentLabel", () => {
      useUIStore.getState().setRenameDialog({ nodeId: "n-7", currentLabel: "My Node" })
      expect(useUIStore.getState().renameDialog).toEqual({
        nodeId: "n-7",
        currentLabel: "My Node",
      })
    })

    it("setRenameDialog(null) clears the dialog", () => {
      useUIStore.getState().setRenameDialog({ nodeId: "n-7", currentLabel: "My Node" })
      useUIStore.getState().setRenameDialog(null)
      expect(useUIStore.getState().renameDialog).toBeNull()
    })

    it("replacing one dialog with another updates atomically", () => {
      useUIStore.getState().setRenameDialog({ nodeId: "n-1", currentLabel: "First" })
      useUIStore.getState().setRenameDialog({ nodeId: "n-2", currentLabel: "Second" })
      expect(useUIStore.getState().renameDialog).toEqual({
        nodeId: "n-2",
        currentLabel: "Second",
      })
    })
  })
})
