import { useState, type DragEvent } from "react"

/** How a row takes part in dragging its list into a new order. */
export interface RowDrag {
  onStart: (event: DragEvent<HTMLElement>) => void
  onOver: (event: DragEvent<HTMLElement>) => void
  onDrop: (event: DragEvent<HTMLElement>) => void
  onEnd: () => void
  /** Another row is being held over this one. */
  target: boolean
  /** This row is the one being dragged. */
  dragging: boolean
}

/**
 * Drag a list's rows into a new order: a row picked up and dropped on another row is put
 * there. `move` gets the dragged row's index and the index it moves to. The step editor's
 * cards and the workbench's field order share it.
 */
export default function useListReorder(move: (from: number, to: number) => void): (index: number) => RowDrag {
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [dropIndex, setDropIndex] = useState<number | null>(null)
  return (index) => ({
    onStart: (event) => {
      event.dataTransfer?.setData("text/plain", String(index))
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"
      setDragIndex(index)
    },
    onOver: (event) => {
      if (dragIndex === null) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move"
      if (dropIndex !== index) setDropIndex(index)
    },
    onDrop: (event) => {
      event.preventDefault()
      if (dragIndex !== null) move(dragIndex, index)
      setDragIndex(null)
      setDropIndex(null)
    },
    onEnd: () => {
      setDragIndex(null)
      setDropIndex(null)
    },
    target: dropIndex === index && dragIndex !== null && dragIndex !== index,
    dragging: dragIndex === index,
  })
}
