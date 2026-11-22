/**
 * An interactive build's models (specs/frontend-modelling-optimiser-ui, specs/modelling):
 * the node's config holds the settings its models share once, and each model under
 * `models` with its own factors, directions and parameters. The editor edits one model at
 * a time through the flat config the backend trains from (`interactive_model_config`).
 */
import { CHART_COLORS } from "../theme/colors"

/** The keys each model holds for itself; the node holds the rest. Mirrors `_train_config.py`. */
export const INTERACTIVE_MODEL_KEYS = [
  "algorithm",
  "feature_columns",
  "params",
  "tuning",
  "stages",
  "monotone_constraints",
  "feature_weights",
] as const

const MODEL_KEYS: ReadonlySet<string> = new Set(INTERACTIVE_MODEL_KEYS)

export interface InteractiveModel {
  id: string
  name: string
  algorithm: string
  [key: string]: unknown
}

/** Whether an interactive build lists its models; one made before models did not. */
export function hasModels(config: Record<string, unknown>): boolean {
  return Array.isArray(config.models) && config.models.length > 0
}

/** The node's models, in order; an interactive build without them is not one the view can open. */
export function interactiveModels(config: Record<string, unknown>): InteractiveModel[] {
  const models = config.models
  if (!Array.isArray(models) || models.length === 0) {
    throw new Error("This interactive build has no models: its config needs `models`.")
  }
  return models as InteractiveModel[]
}

/** The chosen model, or the first while none is chosen or the chosen one has gone (an undo). */
export function chosenModel(config: Record<string, unknown>, modelId: string | null): InteractiveModel {
  const models = interactiveModels(config)
  return models.find((model) => model.id === modelId) ?? models[0]
}

/** The flat config one model trains from: the shared settings with its own over them. */
export function modelConfig(config: Record<string, unknown>, model: InteractiveModel): Record<string, unknown> {
  const flat: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(config)) if (key !== "models") flat[key] = value
  for (const key of INTERACTIVE_MODEL_KEYS) if (key in model) flat[key] = model[key]
  // The model's name is its label, not a setting: renaming it leaves its run current.
  return flat
}

/**
 * The node's config after an edit made to one model's flat config: each key goes to its
 * home, the model's own or the node's. An `undefined` value removes the key.
 */
export function applyModelUpdate(
  config: Record<string, unknown>,
  modelId: string,
  updates: Record<string, unknown>,
): Record<string, unknown> {
  const own: Record<string, unknown> = {}
  const shared: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(updates)) {
    // The run's name is the model's, renamed on its entry, never through the editor.
    if (key === "name" || key === "models") throw new Error(`The model's editor cannot set ${key}.`)
    if (MODEL_KEYS.has(key)) own[key] = value
    else shared[key] = value
  }
  const next = withoutUndefined({ ...config, ...shared })
  next.models = interactiveModels(config).map((model) => (
    model.id === modelId ? withoutUndefined({ ...model, ...own }) as InteractiveModel : model
  ))
  return next
}

function withoutUndefined(record: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(record).filter(([, value]) => value !== undefined))
}

/**
 * A new model's id: random, never derived from the list, so a model added after one was
 * removed never takes the removed model's id, and with it its run still in the store.
 */
function newModelId(models: readonly InteractiveModel[]): string {
  const taken = new Set(models.map((model) => model.id))
  let id: string
  do id = `m${crypto.randomUUID().slice(0, 8)}`
  while (taken.has(id))
  return id
}

/** `base`, or `base 2`, `base 3` and on, whichever no model has. */
export function freeModelName(models: readonly InteractiveModel[], base: string): string {
  const taken = new Set(models.map((model) => model.name))
  if (!taken.has(base)) return base
  let index = 2
  while (taken.has(`${base} ${index}`)) index += 1
  return `${base} ${index}`
}

/**
 * The config with a copy of `source` added after the others, under `name`: its model type,
 * factors, directions and parameters, never its run.
 */
export function addModelCopy(
  config: Record<string, unknown>,
  source: InteractiveModel,
  name: string,
): { config: Record<string, unknown>; model: InteractiveModel } {
  const models = interactiveModels(config)
  if (models.some((model) => model.name === name)) throw new Error(`A model is already named ${name}.`)
  const model: InteractiveModel = { ...structuredClone(source), id: newModelId(models), name }
  return { config: { ...config, models: [...models, model] }, model }
}

export function renameModel(config: Record<string, unknown>, modelId: string, name: string): Record<string, unknown> {
  const models = interactiveModels(config)
  if (models.some((model) => model.id !== modelId && model.name === name)) {
    throw new Error(`A model is already named ${name}.`)
  }
  return { ...config, models: models.map((model) => (model.id === modelId ? { ...model, name } : model)) }
}

export function removeModel(config: Record<string, unknown>, modelId: string): Record<string, unknown> {
  const models = interactiveModels(config)
  if (models.length === 1) throw new Error("An interactive build keeps at least one model.")
  return { ...config, models: models.filter((model) => model.id !== modelId) }
}

/** The key a model's training and run are kept under: its node's, then its id. */
export function modelResultKey(nodeResultKey: string, modelId: string): string {
  return `${nodeResultKey}#model:${modelId}`
}

/** Whether `key` is one of a node's models' keys, the node kept under `nodeResultKey`. */
export function isModelKeyOf(key: string, nodeResultKey: string): boolean {
  return key.startsWith(`${nodeResultKey}#model:`)
}

/** A model's colour on its entry and every chart, by its place among the node's models. */
export function modelColor(index: number): string {
  const palette = CHART_COLORS.optimiserSeries
  return palette[index % palette.length]
}
