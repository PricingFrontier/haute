import { useEffect } from "react"

function carriesFiles(event: DragEvent): boolean {
  return event.dataTransfer !== null && Array.from(event.dataTransfer.types).includes("Files")
}

/**
 * A file dropped where nothing takes files would make the browser open it in place of the
 * editor, losing unsaved work. A drag of files that no drop target handled is prevented here,
 * with the drop effect `none`; a target that takes files prevents the event itself first.
 */
export default function useWindowFileDropGuard(): void {
  useEffect(() => {
    const guard = (event: DragEvent) => {
      if (event.defaultPrevented || !carriesFiles(event)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = "none"
    }
    window.addEventListener("dragover", guard)
    window.addEventListener("drop", guard)
    return () => {
      window.removeEventListener("dragover", guard)
      window.removeEventListener("drop", guard)
    }
  }, [])
}
