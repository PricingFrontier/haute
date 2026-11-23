import { hashConfig } from "../utils/configHash"
/** Canonical stage drafts and explicit migration from the original flat t-boost form. */
export const STAGES = ["full", "pruned", "banded", "smoothed"] as const
export type ModelStage = typeof STAGES[number]
export type PostFitStage = Exclude<ModelStage, "full">
export const STAGE_LABELS: Record<ModelStage, string> = {
  full: "Full GBM", pruned: "Pruned", banded: "Banded", smoothed: "Smoothed",
}
/** A t-boost operation's storage form; a pruning operation records its path settings. */
export interface StageRecipeOperation {
  stage: Exclude<ModelStage, "full">
  parent: ModelStage
  settings: Record<string, unknown>
  path?: StageSettings["path"]
}
/** A t-boost recipe's storage form: the applied policy, path settings included. */
export interface StageRecipe {
  schema_version: 1
  operations: StageRecipeOperation[]
}
export const EMPTY_RECIPE: StageRecipe = { schema_version: 1, operations: [] }
export interface StageSettings {
  schema_version: 2
  enabled: Record<PostFitStage, boolean>
  path: { steps: number; main_effects: boolean; min_rows: number }
  pruning: { fraction: number; tolerance: number; max_tables: number | null }
  banding: { parent: "full" | "pruned"; tolerance: number; deviance_cap: number }
  smoothing: { parent: "full" | "pruned" | "banded"; alpha: number | null; high_order_alpha: number; strength?: number }
  selection?: { relative_tolerance: number; absolute_tolerance: number }
  applied_recipe: StageRecipe
}

const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}

/** Return new drafts; committing them is an explicit editor action. */
export function stageConfiguration(config: Record<string, unknown>): { params: Record<string, unknown>; stages: StageSettings } {
  const params = { ...object(config.params) }
  const raw = object(config.stages)
  if (raw.schema_version !== undefined && raw.schema_version !== 2) throw new Error("Unsupported stage settings version. Update Haute before editing this model.")
  const stages: StageSettings = {
    schema_version: 2,
    enabled: { pruned: true, banded: true, smoothed: true },
    path: { steps: 32, main_effects: false, min_rows: 500, ...object(raw.path) },
    pruning: { fraction: .995, tolerance: .001, max_tables: null, ...object(raw.pruning) },
    banding: { parent: "pruned", tolerance: .75, deviance_cap: .001, ...object(raw.banding) },
    smoothing: { parent: "banded", alpha: null, high_order_alpha: 0, strength: 1, ...object(raw.smoothing) },
    selection: { relative_tolerance: .001, absolute_tolerance: 1e-10, ...object(raw.selection) },
    applied_recipe: structuredClone((raw.applied_recipe ?? EMPTY_RECIPE) as StageRecipe),
  }
  const moved: Record<string, [keyof Omit<StageSettings, "schema_version" | "applied_recipe" | "enabled" | "selection">, string]> = {
    prune_path_steps: ["path", "steps"], prune_main_effects: ["path", "main_effects"],
    prune_guard_min_rows: ["path", "min_rows"], prune_path_fraction: ["pruning", "fraction"],
    prune_path_tolerance: ["pruning", "tolerance"], band_tolerance: ["banding", "tolerance"],
    band_deviance_cap: ["banding", "deviance_cap"], graduation_alpha: ["smoothing", "alpha"],
    graduation_high_order_alpha: ["smoothing", "high_order_alpha"],
  }
  for (const [key, value] of Object.entries(params)) {
    if (key in moved) {
      const [section, setting] = moved[key]
      if (value !== null && !(setting in object(raw[section]))) Object.assign(stages[section], { [setting]: value })
      delete params[key]
    } else if (key === "prune" || key === "graduate" || (key === "prune_selector" && value === "ranked_path")) {
      delete params[key]
    } else if (/^(prune_|band_|graduation_)/.test(key)) {
      throw new Error(`${key} is not supported by staged training. Remove it from the GBM parameters before building.`)
    }
  }
  if (raw.enabled === undefined) {
    stages.enabled.banded = stages.smoothing.parent === "banded"
    stages.enabled.pruned = stages.enabled.banded
      ? stages.banding.parent === "pruned" : stages.smoothing.parent === "pruned"
  } else {
    if (raw.enabled === null || typeof raw.enabled !== "object" || Array.isArray(raw.enabled)
      || Object.entries(raw.enabled).some(([key, value]) => !Object.hasOwn(stages.enabled, key) || typeof value !== "boolean")) {
      throw new Error("Stage enable settings must contain boolean values for pruned, banded and smoothed.")
    }
    Object.assign(stages.enabled, raw.enabled)
  }
  return { params, stages: resolveStageParents(stages) }
}

/** Phase choices change draft routing, never the already applied recipe. */
export function resolveStageParents(settings: StageSettings): StageSettings {
  return {
    ...settings,
    banding: { ...settings.banding, parent: settings.enabled.pruned ? "pruned" : "full" },
    smoothing: { ...settings.smoothing, parent: settings.enabled.banded ? "banded" : settings.enabled.pruned ? "pruned" : "full" },
  }
}


export function nextEnabledStage(settings: StageSettings, applied: ModelStage): PostFitStage | null {
  return STAGES.slice(STAGES.indexOf(applied) + 1).find((stage) => stage !== "full" && settings.enabled[stage]) as PostFitStage | undefined ?? null
}

/** The pruning-path settings a configuration's applied model was built with, else its draft's. */
export function appliedPath(configuration: Record<string, unknown>): unknown {
  const recipe = object(configuration.applied_recipe) as Partial<StageRecipe>
  return recipe.operations?.find((operation) => operation.stage === "pruned")?.path ?? configuration.path
}

/** A built stage's own settings, with a pruning stage's recorded path settings. */
export function savedStageSettings(snapshot: Record<string, unknown>): Record<string, unknown> {
  const operation = object(snapshot.operation)
  return { ...object(operation.settings), ...(operation.path === undefined ? {} : { path: operation.path }) }
}

/** Whether a built stage's operation is the one the draft would build now. */
export function stageMatchesDraft(settings: StageSettings, stage: PostFitStage, snapshot: Record<string, unknown>): boolean {
  const section = stage === "pruned" ? "pruning" : stage === "banded" ? "banding" : "smoothing"
  const { parent = "full", ...draft } = settings[section] as unknown as Record<string, unknown>
  const operation = object(snapshot.operation)
  const actual = { ...object(operation.settings) }
  if (stage === "smoothed") { actual.strength ??= 1; draft.strength ??= 1 }
  return operation.parent === parent && hashConfig({ settings: actual }) === hashConfig({ settings: draft })
    && (stage !== "pruned" || hashConfig({ path: operation.path }) === hashConfig({ path: settings.path }))
}
