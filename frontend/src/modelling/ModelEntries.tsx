/**
 * The left palette's models (specs/frontend-modelling-optimiser-ui): an entry per model of the
 * interactive build, in its chart colour, with the box that puts it on the charts, a bar
 * while it trains, and expandable controls (Duplicate, Rename, Remove); then Add.
 */
import { useId, useLayoutEffect, useRef, useState, type FormEvent } from "react"
import { SlidersHorizontal } from "lucide-react"
import { IconButton } from "../workbench/schema/IconButton"
import { confirmed } from "../stores/useQuestionStore"
import ModalShell from "../components/ModalShell"
import { ModalFormActions, ModalFormHeader, ModalTextInput } from "../components/ModalForm"
import { modelColor, type InteractiveModel } from "./interactiveModels"
import { useModelRunState } from "./modelRuns"
import AddModelPicker from "./AddModelPicker"
import { StageVisibility } from "./StageVisibility"
import "./disclosures.css"

export function ModelEntries({ nodeId, models, chosenId, hiddenIds, readOnly, onChoose, onShow, onAdd, onDuplicate, onRename, onRemove }: {
  nodeId: string
  models: readonly InteractiveModel[]
  chosenId: string
  /** The models whose box is unticked, left off the charts. */
  hiddenIds: readonly string[]
  /** The pipeline cannot be edited: models can be chosen, not added, renamed or removed. */
  readOnly: boolean
  onChoose: (modelId: string) => void
  /** Tick or untick a model's box; looking, not editing, so it works while read-only. */
  onShow: (modelId: string, shown: boolean) => void
  onAdd: () => void
  onDuplicate: (modelId: string) => void
  /** Renames a model; a message when the name cannot be taken. */
  onRename: (modelId: string, name: string) => string | null
  onRemove: (modelId: string) => void
}) {
  // A confirmation may outlive a render: remove from the current model list only.
  const removalContext = { nodeId, models, readOnly, onRemove }
  const currentRemoval = useRef<typeof removalContext | null>(removalContext)
  useLayoutEffect(() => {
    currentRemoval.current = { nodeId, models, readOnly, onRemove }
    return () => { currentRemoval.current = null }
  }, [nodeId, models, readOnly, onRemove])
  const confirmRemoval = async (model: InteractiveModel) => {
    if (!(await confirmed({
      title: `Remove model "${model.name}"?`,
      text: "This removes the model and its settings from this modelling node.",
      confirm: "Remove",
      danger: true,
    }))) return
    const current = currentRemoval.current
    if (current === null || current.nodeId !== nodeId || current.readOnly || current.models.length < 2 || !current.models.some((entry) => entry.id === model.id)) return
    current.onRemove(model.id)
  }
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<InteractiveModel | null>(null)
  return (
    <div role="group" aria-label="Models" className="flex min-w-0 flex-col gap-0.5">
      {models.map((model, index) => (
        <ModelEntry
          key={model.id}
          nodeId={nodeId}
          model={model}
          color={modelColor(index)}
          chosen={model.id === chosenId}
          expanded={model.id === expandedId}
          shown={!hiddenIds.includes(model.id)}
          readOnly={readOnly}
          removable={models.length > 1}
          onChoose={() => {
            setExpandedId(null)
            onChoose(model.id)
          }}
          onToggleActions={() => setExpandedId(model.id === expandedId ? null : model.id)}
          onShow={(shown) => onShow(model.id, shown)}
          onDuplicate={() => onDuplicate(model.id)}
          onRename={() => setRenaming(model)}
          onRemove={() => { void confirmRemoval(model) }}
        />
      ))}
      <AddModelPicker disabled={readOnly} onAdd={onAdd} />
      {renaming !== null && (
        <RenameDialog
          model={renaming}
          onCancel={() => setRenaming(null)}
          onRename={(name) => {
            const refusal = onRename(renaming.id, name)
            if (refusal === null) setRenaming(null)
            return refusal
          }}
        />
      )}
    </div>
  )
}

/**
 * A model's entry: its colour and name, the box that puts it on the charts, and a bar while
 * it trains. The name selects it and the settings icon expands its actions; a failed run marks the name.
 */
function ModelEntry({ nodeId, model, color, chosen, expanded, shown, readOnly, removable, onChoose, onToggleActions, onShow, onDuplicate, onRename, onRemove }: {
  nodeId: string
  model: InteractiveModel
  color: string
  chosen: boolean
  expanded: boolean
  shown: boolean
  readOnly: boolean
  removable: boolean
  onChoose: () => void
  onToggleActions: () => void
  onShow: (shown: boolean) => void
  onDuplicate: () => void
  onRename: () => void
  onRemove: () => void
}) {
  const { state, progress } = useModelRunState(nodeId, model.id)
  const controlsId = useId()
  return (
    <div
      className="modelling-disclosure-row relative min-w-0 rounded-lg text-[12px]"
      data-selected={chosen}
    >
      <div className="flex min-w-0 items-center">
        <button
          type="button"
          aria-pressed={chosen}
          title={state === "Failed" ? `${model.name}: its last run failed` : model.name}
          className="modelling-disclosure-name focus-ring flex min-w-0 flex-1 flex-col justify-center gap-1 rounded-lg px-2 py-1.5 text-left text-[12px] leading-4"
          onClick={onChoose}
        >
          <span className="flex min-w-0 items-center gap-1.5">
            <span aria-hidden="true" className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
            <span
              className={expanded ? "min-w-0 break-all" : "min-w-0 truncate"}
              style={{ color: state === "Failed" ? "var(--danger-text-soft)" : chosen ? "var(--model-accent)" : "var(--text-primary)" }}
            >
              {model.name}
            </span>
          </span>
          {state === "Training" && (
            <span
              role="progressbar"
              aria-label={`${model.name} training`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress * 100)}
              className="block h-1 w-full overflow-hidden rounded-full"
              style={{ background: "var(--border)" }}
            >
              <span className="block h-full transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${Math.max(progress * 100, 2)}%`, background: "var(--model-accent)" }} />
            </span>
          )}
        </button>
        <IconButton
          icon={SlidersHorizontal}
          onClick={onToggleActions}
          aria-label={`${model.name} settings`}
          title="Model actions"
          aria-expanded={expanded}
          aria-controls={controlsId}
          className="mr-1"
          style={{ color: expanded ? "var(--text-primary)" : "var(--text-muted)" }}
        />
        <input
          type="checkbox"
          aria-label={`Show ${model.name} on the charts`}
          title={`Show ${model.name} on the charts`}
          className="mr-2 h-3 w-3 shrink-0"
          style={{ accentColor: "var(--model-accent)" }}
          checked={shown}
          onChange={(event) => onShow(event.target.checked)}
        />
      </div>
      <div id={controlsId} className="modelling-disclosure-body" data-expanded={expanded} aria-hidden={!expanded} inert={!expanded ? true : undefined}>
        <div className="min-h-0 overflow-hidden">
          <div role="group" aria-label={`${model.name} controls`} className="mx-2 space-y-2 border-t pb-2.5 pt-2 text-[11px]" style={{ borderColor: "var(--model-accent-border)" }}>
            <StageVisibility nodeId={nodeId} modelId={model.id} />
            <div className="flex flex-col gap-1" style={{ color: "var(--text-muted)" }}>
              <button type="button" disabled={readOnly} className="hover-chrome focus-ring rounded px-1 py-1 text-left disabled:opacity-40" onClick={onDuplicate}>Duplicate</button>
              <button type="button" disabled={readOnly} className="hover-chrome focus-ring rounded px-1 py-1 text-left disabled:opacity-40" onClick={onRename}>Rename</button>
              {removable && <button type="button" disabled={readOnly} className="hover-chrome focus-ring rounded px-1 py-1 text-left disabled:opacity-40" style={{ color: "var(--danger-text-soft)" }} onClick={onRemove}>Remove</button>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function RenameDialog({ model, onCancel, onRename }: {
  model: InteractiveModel
  onCancel: () => void
  /** A message when the name cannot be taken. */
  onRename: (name: string) => string | null
}) {
  const [name, setName] = useState(model.name)
  const [refusal, setRefusal] = useState<string | null>(null)
  const trimmed = name.trim()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (trimmed === "") return
    setRefusal(onRename(trimmed))
  }
  return (
    <ModalShell ariaLabel={`Rename ${model.name}`} onClose={onCancel}>
      <form onSubmit={submit}>
        <ModalFormHeader title={`Rename ${model.name}`} />
        <div className="space-y-2 px-4 py-3">
          <ModalTextInput aria-label="Model name" value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          {refusal !== null && (
            <p role="alert" className="text-[12px]" style={{ color: "var(--danger-text-soft)" }}>{refusal}</p>
          )}
          <ModalFormActions
            onCancel={onCancel}
            submitLabel="Rename"
            busyLabel="Renaming"
            busy={false}
            disabled={trimmed === "" || trimmed === model.name}
            submitTestId="rename-model-submit"
          />
        </div>
      </form>
    </ModalShell>
  )
}
