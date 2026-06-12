/**
 * Pure-logic tests for the variable-width column virtualisation module
 * (panels/dataPreviewColumns.ts) introduced by the DataPreview draggable
 * column-resize work.
 *
 * Spec: notes design `datapreview-column-resize` §5.2. The keystone for the
 * no-overrides case is BEHAVIOURAL INVARIANTS, not byte-parity with the old
 * ceil-based uniform formulae — three divergences are deliberate and pinned
 * below as named hand-computed fixtures (§3.2 of the design):
 *   1. off-by-one at non-column-aligned scroll,
 *   2. beyond-max scroll clamp shape,
 *   3. virtualise-or-not gate boundary.
 */
import { describe, it, expect } from "vitest"
import {
  buildColumnOffsets,
  findColumnIndex,
  getColumnWindowVariable,
  ROW_NUMBER_WIDTH,
  COLUMN_OVERSCAN,
  COL_MIN_OVERRIDE,
  COL_MAX_OVERRIDE,
  clampColumnWidth,
  type ColumnOffsets,
  type ColumnWindow,
} from "../../panels/dataPreviewColumns"

function makeColumns(count: number): { name: string }[] {
  return Array.from({ length: count }, (_, i) => ({ name: `col_${i}` }))
}

function uniformOffsets(count: number, width: number): ColumnOffsets {
  return buildColumnOffsets(makeColumns(count), width, {}, null)
}

/** Columns whose span [offsets[i], offsets[i+1]) intersects [spanStart, spanEnd). */
function intersectingColumns(off: ColumnOffsets, spanStart: number, spanEnd: number): number[] {
  const out: number[] = []
  for (let i = 0; i + 1 < off.offsets.length; i++) {
    if (off.offsets[i] < spanEnd && off.offsets[i + 1] > spanStart) out.push(i)
  }
  return out
}

/**
 * The behavioural invariants every window must satisfy (design §5.2-1/-4):
 *  - bounds: 0 <= startIdx <= endIdx <= columnCount
 *  - superset: every column intersecting the visible span is inside the window
 *  - pads sum: leftPad + widths of windowed columns + rightPad === totalColumnsWidth
 *  - pads non-negative
 *  - totalWidth = ROW_NUMBER_WIDTH + totalColumnsWidth
 */
function assertWindowInvariants(
  off: ColumnOffsets,
  columnCount: number,
  scrollLeft: number,
  viewWidth: number,
  win: ColumnWindow,
): void {
  const label = `count=${columnCount} scrollLeft=${scrollLeft} viewWidth=${viewWidth}`
  expect(win.startIdx, label).toBeGreaterThanOrEqual(0)
  expect(win.endIdx, label).toBeGreaterThanOrEqual(win.startIdx)
  expect(win.endIdx, label).toBeLessThanOrEqual(columnCount)

  const visibleWidth = Math.max(off.defaultWidth, viewWidth - ROW_NUMBER_WIDTH)
  const dataScrollLeft = Math.max(0, scrollLeft - ROW_NUMBER_WIDTH)
  for (const i of intersectingColumns(off, dataScrollLeft, dataScrollLeft + visibleWidth)) {
    expect(i, `${label}: visible column ${i} must be inside window [${win.startIdx}, ${win.endIdx})`).toBeGreaterThanOrEqual(win.startIdx)
    expect(i, `${label}: visible column ${i} must be inside window [${win.startIdx}, ${win.endIdx})`).toBeLessThan(win.endIdx)
  }

  const windowedWidth = off.offsets[win.endIdx] - off.offsets[win.startIdx]
  expect(win.leftPad, label).toBeGreaterThanOrEqual(0)
  expect(win.rightPad, label).toBeGreaterThanOrEqual(0)
  expect(win.leftPad + windowedWidth + win.rightPad, label).toBe(off.totalColumnsWidth)
  expect(win.totalWidth, label).toBe(ROW_NUMBER_WIDTH + off.totalColumnsWidth)
}

describe("buildColumnOffsets", () => {
  it("builds a prefix-sum of length columnCount + 1 with offsets[0] = 0", () => {
    const off = uniformOffsets(4, 160)
    expect(off.offsets).toEqual([0, 160, 320, 480, 640])
    expect(off.totalColumnsWidth).toBe(640)
    expect(off.defaultWidth).toBe(160)
    expect(off.uniform).toBe(true)
  })

  it("applies overrides by column name and flags non-uniform", () => {
    const off = buildColumnOffsets(makeColumns(3), 160, { col_1: 640 }, null)
    expect(off.offsets).toEqual([0, 160, 800, 960])
    expect(off.totalColumnsWidth).toBe(960)
    expect(off.uniform).toBe(false)
  })

  it("a live drag override beats the committed override for the same column", () => {
    const off = buildColumnOffsets(makeColumns(2), 160, { col_0: 640 }, { column: "col_0", width: 200 })
    expect(off.offsets).toEqual([0, 200, 360])
    expect(off.uniform).toBe(false)
  })

  it("ignores override entries for columns not present (stale entries are harmless)", () => {
    const off = buildColumnOffsets(makeColumns(2), 160, { vanished: 640 }, null)
    expect(off.offsets).toEqual([0, 160, 320])
    expect(off.uniform).toBe(true)
  })

  it("handles zero columns", () => {
    const off = uniformOffsets(0, 160)
    expect(off.offsets).toEqual([0])
    expect(off.totalColumnsWidth).toBe(0)
  })
})

describe("findColumnIndex", () => {
  const offsets = [0, 160, 320, 480]

  it("returns the largest i with offsets[i] <= x", () => {
    expect(findColumnIndex(offsets, 0)).toBe(0)
    expect(findColumnIndex(offsets, 159)).toBe(0)
    expect(findColumnIndex(offsets, 160)).toBe(1)
    expect(findColumnIndex(offsets, 161)).toBe(1)
    expect(findColumnIndex(offsets, 479)).toBe(2)
  })

  it("saturates at the last offset for x at or beyond the total", () => {
    expect(findColumnIndex(offsets, 480)).toBe(3)
    expect(findColumnIndex(offsets, 1_000_000)).toBe(3)
  })

  it("saturates at 0 for negative x", () => {
    expect(findColumnIndex(offsets, -5)).toBe(0)
  })

  it("handles duplicate offsets (zero-width entries) by picking the largest index", () => {
    expect(findColumnIndex([0, 100, 100, 200], 100)).toBe(2)
  })

  it("saturates on degenerate arrays", () => {
    expect(findColumnIndex([0], 50)).toBe(0)
    expect(findColumnIndex([], 50)).toBe(0)
  })
})

describe("clampColumnWidth", () => {
  it("clamps into [COL_MIN_OVERRIDE, COL_MAX_OVERRIDE]", () => {
    expect(clampColumnWidth(-200)).toBe(COL_MIN_OVERRIDE)
    expect(clampColumnWidth(60)).toBe(60)
    expect(clampColumnWidth(320)).toBe(320)
    expect(clampColumnWidth(640)).toBe(640)
    expect(clampColumnWidth(5_000)).toBe(COL_MAX_OVERRIDE)
  })
})

describe("getColumnWindowVariable — uniform behavioural invariants (design §5.2-1)", () => {
  const columnCounts = [0, 1, 7, 40, 120, 1000]
  const viewWidths = [300, 720, 900, 1600]
  const widths = [120, 140, 160]

  it("satisfies window-superset, pads-sum, bounds and totalWidth across the sweep grid", () => {
    for (const count of columnCounts) {
      for (const width of widths) {
        const off = uniformOffsets(count, width)
        const totalWidth = ROW_NUMBER_WIDTH + count * width
        expect(off.totalColumnsWidth).toBe(count * width)
        for (const viewWidth of viewWidths) {
          const mid = Math.floor((ROW_NUMBER_WIDTH + off.totalColumnsWidth) / 2)
          const maxScroll = Math.max(0, ROW_NUMBER_WIDTH + off.totalColumnsWidth - viewWidth)
          const beyondMax = ROW_NUMBER_WIDTH + off.totalColumnsWidth + 5_000
          for (const scrollLeft of [0, mid, maxScroll, beyondMax]) {
            const win = getColumnWindowVariable(off, count, scrollLeft, viewWidth)
            expect(win.totalWidth).toBe(totalWidth)
            assertWindowInvariants(off, count, scrollLeft, viewWidth, win)
          }
        }
      }
    }
  })

  it("returns the identical render-everything result whenever the gate is off", () => {
    for (const count of columnCounts) {
      for (const width of widths) {
        const off = uniformOffsets(count, width)
        for (const viewWidth of viewWidths) {
          const visibleWidth = Math.max(width, viewWidth - ROW_NUMBER_WIDTH)
          const gateOff = off.totalColumnsWidth <= visibleWidth + 2 * COLUMN_OVERSCAN * width
          if (!gateOff) continue
          for (const scrollLeft of [0, 5_000]) {
            const win = getColumnWindowVariable(off, count, scrollLeft, viewWidth)
            expect(win).toEqual({
              startIdx: 0,
              endIdx: count,
              leftPad: 0,
              rightPad: 0,
              totalWidth: ROW_NUMBER_WIDTH + count * width,
            })
          }
        }
      }
    }
  })
})

describe("getColumnWindowVariable — hand-computed fixtures pinning the §3.2 divergences", () => {
  it("divergence 1: misaligned scroll renders one extra intersected column (120 cols / 160px / viewWidth 960 / scrollLeft 12800 -> [76, 89))", () => {
    const off = uniformOffsets(120, 160)
    const win = getColumnWindowVariable(off, 120, 12_800, 960)
    expect(win).toEqual({
      startIdx: 76,
      endIdx: 89,
      leftPad: 76 * 160, // 12160
      rightPad: (120 - 89) * 160, // 4960
      totalWidth: ROW_NUMBER_WIDTH + 120 * 160, // 19248
    })
  })

  it("divergence 2: beyond-max scroll saturates instead of back-filling a full window (-> [117, 120), leftPad 18720)", () => {
    const off = uniformOffsets(120, 160)
    const win = getColumnWindowVariable(off, 120, 100_000, 960)
    expect(win).toEqual({
      startIdx: 117,
      endIdx: 120,
      leftPad: 18_720,
      rightPad: 0,
      totalWidth: 19_248,
    })
  })

  it("divergence 3: the width-aware gate virtualises 12 cols / 160px / viewWidth 960 (the old count gate rendered everything)", () => {
    const off = uniformOffsets(12, 160)
    const win = getColumnWindowVariable(off, 12, 0, 960)
    // 12 * 160 = 1920 > (960 - 48) + 2 * 3 * 160 = 1872 -> virtualised.
    expect(win).toEqual({
      startIdx: 0,
      endIdx: 9, // rawEnd = findColumnIndex(912) + 1 = 6, + overscan 3
      leftPad: 0,
      rightPad: 480,
      totalWidth: ROW_NUMBER_WIDTH + 12 * 160,
    })
  })
})

describe("getColumnWindowVariable — override windows (design §5.2-3)", () => {
  it("a 640px column before the window shifts offsets without losing visible columns", () => {
    const off = buildColumnOffsets(makeColumns(120), 160, { col_0: 640 }, null)
    const win = getColumnWindowVariable(off, 120, 13_440, 960)
    expect(win).toEqual({
      startIdx: 77,
      endIdx: 90,
      leftPad: 640 + 76 * 160, // 12800
      rightPad: 19_680 - (640 + 89 * 160), // 4800
      totalWidth: ROW_NUMBER_WIDTH + 640 + 119 * 160, // 19728
    })
    assertWindowInvariants(off, 120, 13_440, 960, win)
  })

  it("a 640px column inside the window stays rendered", () => {
    const off = buildColumnOffsets(makeColumns(120), 160, { col_2: 640 }, null)
    const win = getColumnWindowVariable(off, 120, 0, 960)
    expect(win.startIdx).toBe(0)
    // Visible span [0, 912) intersects cols 0..2 (col_2 spans [320, 960)).
    expect(win.endIdx).toBeGreaterThanOrEqual(3)
    assertWindowInvariants(off, 120, 0, 960, win)
  })

  it("a 640px column after the window leaves the window untouched", () => {
    const off = buildColumnOffsets(makeColumns(120), 160, { col_119: 640 }, null)
    const win = getColumnWindowVariable(off, 120, 0, 960)
    expect(win.startIdx).toBe(0)
    expect(win.endIdx).toBe(9) // same shape as the uniform left-edge window
    assertWindowInvariants(off, 120, 0, 960, win)
  })

  it("shrinking every column to 60px widens the window in index terms to fill the viewport", () => {
    const overrides = Object.fromEntries(makeColumns(120).map((c) => [c.name, 60]))
    const off = buildColumnOffsets(makeColumns(120), 160, overrides, null)
    expect(off.totalColumnsWidth).toBe(7_200)
    const win = getColumnWindowVariable(off, 120, 3_048, 960)
    expect(win).toEqual({
      startIdx: 47,
      endIdx: 69,
      leftPad: 47 * 60, // 2820
      rightPad: (120 - 69) * 60, // 3060
      totalWidth: ROW_NUMBER_WIDTH + 7_200,
    })
    // 22 columns windowed — wider in index terms than the 13 a uniform 160px window holds.
    expect(win.endIdx - win.startIdx).toBeGreaterThan(13)
    assertWindowInvariants(off, 120, 3_048, 960, win)
  })

  it("all columns overridden to the max width still satisfies the invariants", () => {
    const overrides = Object.fromEntries(makeColumns(40).map((c) => [c.name, COL_MAX_OVERRIDE]))
    const off = buildColumnOffsets(makeColumns(40), 160, overrides, null)
    for (const scrollLeft of [0, 10_000, 40 * COL_MAX_OVERRIDE + ROW_NUMBER_WIDTH + 999]) {
      const win = getColumnWindowVariable(off, 40, scrollLeft, 960)
      assertWindowInvariants(off, 40, scrollLeft, 960, win)
    }
  })
})

describe("getColumnWindowVariable — property-style invariant loop (design §5.2-4)", () => {
  it("random override maps and scroll positions never violate the invariants", () => {
    // Deterministic LCG — no new deps, reproducible failures.
    let seed = 0x2f6e2b1
    const rand = (): number => {
      seed = (seed * 1_103_515_245 + 12_345) % 0x80000000
      return seed / 0x80000000
    }

    for (let trial = 0; trial < 200; trial++) {
      const count = 1 + Math.floor(rand() * 300)
      const columns = makeColumns(count)
      const overrides: Record<string, number> = {}
      const overrideCount = Math.floor(rand() * count)
      for (let i = 0; i < overrideCount; i++) {
        const idx = Math.floor(rand() * count)
        overrides[`col_${idx}`] = COL_MIN_OVERRIDE + Math.floor(rand() * (COL_MAX_OVERRIDE - COL_MIN_OVERRIDE))
      }
      const defaultWidth = [120, 140, 160][Math.floor(rand() * 3)]
      const off = buildColumnOffsets(columns, defaultWidth, overrides, null)
      const viewWidth = 300 + Math.floor(rand() * 1_400)
      const scrollLeft = Math.floor(rand() * (off.totalColumnsWidth + ROW_NUMBER_WIDTH + 2_000))
      const win = getColumnWindowVariable(off, count, scrollLeft, viewWidth)
      assertWindowInvariants(off, count, scrollLeft, viewWidth, win)
    }
  })
})

describe("getColumnWindowVariable — shrink clamp (design §5.2-5)", () => {
  it("a scroll position computed against 1000 columns saturates safely when recomputed with 80", () => {
    const scrollLeft = 900 * 160 // far past the 80-column content
    const off = buildColumnOffsets(makeColumns(80), 160, {}, null)
    const win = getColumnWindowVariable(off, 80, scrollLeft, 960)
    expect(win.startIdx).toBe(77)
    expect(win.endIdx).toBe(80)
    expect(win.startIdx).toBeLessThanOrEqual(79)
    assertWindowInvariants(off, 80, scrollLeft, 960, win)
  })
})
