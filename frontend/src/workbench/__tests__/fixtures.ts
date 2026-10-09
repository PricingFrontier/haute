/**
 * A form for the sheet tests (specs/workbench): policy details (one row) shown by a
 * Collection, equipment (many rows, keyed by item) shown by a Table, and premiums (many
 * rows, keyed by item, an output), on one sheet, with a second, empty sheet.
 */
import { vi } from "vitest"
import type { FormSpec, SchemaColumn, SchemaTable } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"

export const column = (id: string, name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  id,
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  index: false,
  ...overrides,
})

export const table = (id: string, name: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({
  id,
  name,
  role: "input",
  rows: "one",
  columns: [],
  ...overrides,
})

export const sheetForm = (): FormSpec => ({
  version: 1,
  name: "motor",
  schema: {
    tables: [
      table("policy", "policy", { columns: [column("policy.state", "state", { options: ["CA", "NY"], required: true }), column("policy.year", "year", { type: "int" })] }),
      table("equipment", "equipment", {
        rows: "many",
        columns: [column("equipment.item", "item", { key: true }), column("equipment.value", "value", { type: "float" })],
      }),
      table("premiums", "premiums", {
        role: "output",
        rows: "many",
        columns: [column("premiums.item", "item", { key: true }), column("premiums.premium", "premium", { type: "float" })],
      }),
    ],
  },
  pages: [
    {
      id: "p1",
      title: "Sheet 1",
      widgets: [
        { id: "w_boxes", type: "collection", x: 16, y: 16, w: 400, h: 120, title: "Policy", columns: 2, fields: [{ table: "policy", column: "policy.state" }] },
        {
          id: "w_grid",
          type: "tableInput",
          x: 16,
          y: 160,
          w: 720,
          h: 200,
          title: "Equipment",
          rows: 3,
          fields: [{ table: "equipment", column: "equipment.item" }, { table: "equipment", column: "equipment.value" }],
        },
      ],
    },
    { id: "p2", title: "Sheet 2", widgets: [] },
  ],
  sample: {},
})

/** Load `form` into the form store as read at `rev-0`, and reset the view store. */
export function loadForm(form: FormSpec = sheetForm()): void {
  useWorkbenchFormStore.setState({
    form,
    revision: "rev-0",
    status: "ready",
    loadError: null,
    savedForm: JSON.stringify(form),
    dirty: false,
    undoStack: [],
    redoStack: [],
    stale: false,
    saving: false,
  })
  useWorkbenchViewStore.setState({ section: "sheets", pageId: null, selectedId: null, zoom: 1, creating: null, sheet: null, viewport: null })
}

export const currentForm = (): FormSpec => {
  const { form } = useWorkbenchFormStore.getState()
  if (form === null) throw new Error("no form")
  return form
}

class MockResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** jsdom has no ResizeObserver and reports every box as empty; give the sheet a viewport. */
export function stubLayout(rect = { left: 0, top: 0, width: 1000, height: 800 }): void {
  vi.stubGlobal("ResizeObserver", MockResizeObserver)
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    ...rect,
    right: rect.left + rect.width,
    bottom: rect.top + rect.height,
    x: rect.left,
    y: rect.top,
    toJSON: () => ({}),
  } as DOMRect)
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => rect.width })
}

/** A pointer drag from (fromX, fromY) on `target` to (toX, toY), released there. */
export function drag(target: Element, from: [number, number], to: [number, number]): void {
  target.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: from[0], clientY: from[1] }))
  window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: to[0], clientY: to[1] }))
  window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: to[0], clientY: to[1] }))
}

/** Release any press a test left unreleased, so its tracker never acts on the next test. */
export function releasePointer(): void {
  window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }))
}
