import { useEffect, useRef, useSyncExternalStore } from "react"
import { ControlsPage } from "./ControlsPage"

/** The catalogue's pages: each one's place in the address, its name in the bar and what it draws. */
const PAGES = [
  { path: "", name: "Front page", Page: FrontPage },
  { path: "controls", name: "Controls", Page: ControlsPage },
]

function watchAddress(onChange: () => void) {
  window.addEventListener("hashchange", onChange)
  return () => window.removeEventListener("hashchange", onChange)
}

/** The page the address names, as in `#/controls`. */
const addressPath = () => window.location.hash.replace(/^#\/?/, "")

/**
 * The kit's catalogue (specs/frontend-shared): haute-ui's pieces, a level to a page, each
 * drawn by the app's own stylesheet in every variant and state. An address naming no
 * page shows the front page. A change of page moves the focus to the page, so it is
 * announced and read from its top.
 */
export function Catalogue() {
  const path = useSyncExternalStore(watchAddress, addressPath)
  const current = PAGES.find((page) => page.path === path) ?? PAGES[0]
  const main = useRef<HTMLElement>(null)
  const shown = useRef(current)
  useEffect(() => {
    if (shown.current === current) return
    shown.current = current
    main.current?.focus()
  }, [current])
  return (
    <div className="catalogue">
      <header className="catalogue-header">
        <h1 className="catalogue-name">haute-ui</h1>
        <nav className="catalogue-nav" aria-label="Catalogue">
          {PAGES.map((page) => (
            <a key={page.path} href={`#/${page.path}`} aria-current={page === current ? "page" : undefined}>
              {page.name}
            </a>
          ))}
        </nav>
      </header>
      <main ref={main} tabIndex={-1} aria-label={current.name} className="catalogue-page">
        <current.Page />
      </main>
    </div>
  )
}

function FrontPage() {
  return (
    <section>
      <h2>What the screens are built from</h2>
      <p>
        haute-ui is the kit the pipeline editor, the workbench and the underwriter app share. This catalogue draws each of
        its pieces with the app's own stylesheet, in every variant and state, so what is here is what the app shows.
      </p>
      <p>A piece is on the page of its level.</p>
      <ul className="catalogue-levels">
        <li>
          <a href="#/controls">Controls</a>: one thing to press. The button, a link drawn as one, the toolbar's button and the toolbar's status.
        </li>
      </ul>
      <p>The kit's toolbar, palette and side panel are not drawn here yet.</p>
    </section>
  )
}
