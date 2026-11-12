import { ArrowLeft, FolderOpen, Plus, RotateCw, Wrench } from "lucide-react"
import type { ReactNode } from "react"
import { Button, ButtonLink, ToolbarButton, ToolbarColumn } from "../src/haute-ui"

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
      </section>
    </>
  )
}
