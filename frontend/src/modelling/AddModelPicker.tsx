import { useId, useLayoutEffect, useRef, useState, type RefObject } from "react"
import { createPortal } from "react-dom"
import { Plus } from "lucide-react"
import { MenuBackdrop } from "../components/BackdropMenu"
import { useMenuKeyboard } from "../hooks/useMenuKeyboard"

/** Choose a supported model type before adding it to the node. */
export default function AddModelPicker({ disabled, onAdd }: { disabled: boolean; onAdd: () => void }) {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const visible = open && !disabled
  const close = () => {
    setOpen(false)
    trigger.current?.focus()
  }
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="toolbar-btn focus-ring mt-2 flex w-full items-center justify-center gap-1 rounded-md px-2 py-1 text-[12px] font-medium"
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={visible}
        aria-controls={visible ? menuId : undefined}
        onClick={() => setOpen(!open)}
      >
        <Plus size={13} aria-hidden="true" />
        Add
      </button>
      {visible && <ModelTypeMenu id={menuId} trigger={trigger} onClose={close} onAdd={() => { close(); onAdd() }} />}
    </>
  )
}

function ModelTypeMenu({ id, trigger, onClose, onAdd }: {
  id: string
  trigger: RefObject<HTMLButtonElement | null>
  onClose: () => void
  onAdd: () => void
}) {
  const menuRef = useRef<HTMLDivElement>(null)
  useMenuKeyboard(menuRef, onClose)
  useLayoutEffect(() => {
    const position = () => {
      const anchor = trigger.current
      const menu = menuRef.current
      if (!anchor || !menu) return
      const rect = anchor.getBoundingClientRect()
      const below = rect.bottom + 4
      menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`
      menu.style.top = `${Math.max(8, below + menu.offsetHeight <= window.innerHeight - 8 ? below : rect.top - menu.offsetHeight - 4)}px`
    }
    position()
    window.addEventListener("resize", position)
    window.addEventListener("scroll", position, true)
    return () => {
      window.removeEventListener("resize", position)
      window.removeEventListener("scroll", position, true)
    }
  }, [trigger])
  return createPortal(
    <>
      <MenuBackdrop onDismiss={onClose} />
      <div
        ref={menuRef}
        id={id}
        role="menu"
        aria-label="Add model"
        className="fixed z-50 w-[200px] max-h-[calc(100vh-16px)] overflow-y-auto rounded-lg py-1 shadow-xl"
        style={{ background: "var(--bg-panel)", border: "1px solid var(--border-bright)" }}
        onKeyDown={(event) => { if (event.key === "Tab") onClose() }}
      >
        <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Add model</div>
        <button type="button" role="menuitem" className="hover-chrome focus-ring block w-full px-3 py-2 text-left text-[12px]" onClick={onAdd}>
          t-boost
        </button>
      </div>
    </>,
    document.body,
  )
}
