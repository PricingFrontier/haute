import "@testing-library/jest-dom/vitest"

const emptyDomRect = (): DOMRect => new DOMRect(0, 0, 0, 0)

const emptyDomRectList = (): DOMRectList => {
  const rects = [] as unknown as DOMRect[] & { item: (index: number) => DOMRect | null }
  rects.item = () => null
  return rects as unknown as DOMRectList
}

if (!Range.prototype.getClientRects) {
  Range.prototype.getClientRects = emptyDomRectList
}

if (!Range.prototype.getBoundingClientRect) {
  Range.prototype.getBoundingClientRect = emptyDomRect
}
