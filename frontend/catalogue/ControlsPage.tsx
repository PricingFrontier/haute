import { ArrowLeft, ChevronDown, FolderOpen, Plus, RotateCw, Wrench } from "lucide-react"
import type { ReactNode } from "react"
import { Button, ButtonLink, ToolbarButton, ToolbarColumn, ToolbarLink, ToolbarStatus } from "../src/haute-ui"

/** One sample on its stage, with what draws it underneath. */
function Sample({ code, stage = "catalogue-stage", children }: { code: string; stage?: string; children: ReactNode }) {
  return (
    <figure className="catalogue-sample">
      <div className={stage}>{children}</div>
      <figcaption>{code}</figcaption>
    </figure>
  )
}

/** The controls: one thing to press. Hover, press and focus are the samples' own. */
export function ControlsPage() {
  return (
    <>
      <section aria-labelledby="button">
        <h2 id="button">Button</h2>
        <p>
          A button outside a toolbar. It is as wide as its label, an icon alone, or a page's main action filled in the
          accent. Hover over one, press it, or tab to it to see those states.
        </p>

        <h3>Shapes</h3>
        <div className="catalogue-samples">
          <Sample code="<Button>">
            <Button>Retry</Button>
          </Sample>
          <Sample code="<Button> with an icon">
            <Button>
              <RotateCw size={12} /> Retry
            </Button>
          </Sample>
          <Sample code='<Button iconOnly aria-label="…">'>
            <Button iconOnly aria-label="Refresh the list">
              <RotateCw size={12} />
            </Button>
          </Sample>
          <Sample code='<Button variant="fill">'>
            <Button variant="fill">
              <Plus size={13} /> New quote
            </Button>
          </Sample>
        </div>

        <h3>Disabled</h3>
        <div className="catalogue-samples">
          <Sample code="<Button disabled>">
            <Button disabled>
              <RotateCw size={12} /> Retry
            </Button>
          </Sample>
          <Sample code="<Button iconOnly disabled>">
            <Button iconOnly disabled aria-label="Open the quote">
              <FolderOpen size={12} />
            </Button>
          </Sample>
          <Sample code='<Button variant="fill" disabled>'>
            <Button variant="fill" disabled>
              <Plus size={13} /> Save
            </Button>
          </Sample>
        </div>

        <h3>Pressed</h3>
        <div className="catalogue-samples">
          <Sample code="<Button aria-pressed>">
            <Button aria-pressed>History</Button>
          </Sample>
          <Sample code="<Button iconOnly aria-pressed>">
            <Button iconOnly aria-pressed aria-label="Show the folder">
              <FolderOpen size={12} />
            </Button>
          </Sample>
        </div>

        <h3>As a link</h3>
        <div className="catalogue-samples">
          <Sample code='<ButtonLink href="…">'>
            <ButtonLink href="#/controls">
              <ArrowLeft size={12} /> Back to Quotes
            </ButtonLink>
          </Sample>
          <Sample code='<ButtonLink href="…" variant="fill">'>
            <ButtonLink href="#/controls" variant="fill">
              <Plus size={13} /> New quote
            </ButtonLink>
          </Sample>
        </div>

        <h3>Laid out by its caller</h3>
        <div className="catalogue-samples catalogue-samples-wide">
          <Sample code='<Button className="ml-auto"> in a row' stage="catalogue-stage-wide">
            <Button>Discard</Button>
            <Button className="ml-auto">Save note</Button>
          </Sample>
          <Sample code='<Button className="self-start"> in a column' stage="catalogue-stage-column">
            <Button className="self-start">Replace</Button>
            <Button>Run all</Button>
          </Sample>
        </div>
      </section>

      <section aria-labelledby="toolbar-button">
        <h2 id="toolbar-button">Toolbar button</h2>
        <p>The toolbar's own button. It is as wide as its column, so the two buttons of a column match.</p>
        <div className="catalogue-samples">
          <Sample code="<ToolbarButton icon={…}> in a <ToolbarColumn>">
            <ToolbarColumn>
              <ToolbarButton icon={Wrench}>Build</ToolbarButton>
              <ToolbarButton icon={RotateCw}>Refresh</ToolbarButton>
            </ToolbarColumn>
          </Sample>
          <Sample code="<ToolbarButton compact>">
            <ToolbarColumn>
              <ToolbarButton compact icon={Plus}>
                Zoom In
              </ToolbarButton>
            </ToolbarColumn>
          </Sample>
          <Sample code="<ToolbarButton disabled>">
            <ToolbarColumn>
              <ToolbarButton icon={Wrench} disabled>
                Build
              </ToolbarButton>
            </ToolbarColumn>
          </Sample>
          <Sample code="<ToolbarButton aria-pressed>">
            <ToolbarColumn>
              <ToolbarButton icon={Wrench} aria-pressed>
                Build
              </ToolbarButton>
            </ToolbarColumn>
          </Sample>
        </div>

        <h3>As a link</h3>
        <div className="catalogue-samples">
          <Sample code='<ToolbarLink icon={…} href="…" className="w-auto">'>
            <ToolbarLink icon={ArrowLeft} href="#/controls" className="w-auto">
              Quotes
            </ToolbarLink>
          </Sample>
        </div>

        <h3>Laid out by its caller</h3>
        <div className="catalogue-samples">
          <Sample code='<ToolbarButton className="justify-between"> over another'>
            <ToolbarColumn>
              <ToolbarButton className="justify-between">
                development <ChevronDown size={11} />
              </ToolbarButton>
              <ToolbarButton>Pipeline</ToolbarButton>
            </ToolbarColumn>
          </Sample>
          <Sample code='<ToolbarButton compact className="justify-start">'>
            <ToolbarColumn className="w-40">
              <ToolbarButton compact icon={Wrench} className="justify-start">
                Workbench
              </ToolbarButton>
            </ToolbarColumn>
          </Sample>
        </div>
      </section>

      <section aria-labelledby="toolbar-status">
        <h2 id="toolbar-status">Toolbar status</h2>
        <p>
          A status drawn in a toolbar button's shape, so it lines up with the buttons beside it. It is a label: it says
          what is so, and anything to press sits inside it.
        </p>
        <div className="catalogue-samples">
          <Sample code="<ToolbarStatus> over a <ToolbarButton>">
            <ToolbarColumn>
              <ToolbarStatus>Checking Git…</ToolbarStatus>
              <ToolbarButton icon={Wrench}>Build</ToolbarButton>
            </ToolbarColumn>
          </Sample>
          <Sample code="<ToolbarStatus> holding something to press">
            <ToolbarColumn>
              <ToolbarStatus>
                Files in use
                <button type="button" className="underline">
                  Retry
                </button>
              </ToolbarStatus>
            </ToolbarColumn>
          </Sample>
        </div>
      </section>
    </>
  )
}
