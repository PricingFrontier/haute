import { hashConfig } from "../utils/configHash"
/** Canonical stage drafts and explicit migration from the original flat t-boost form. */
export const STAGES = ["full", "pruned", "banded", "smoothed"] as const
export type ModelStage = typeof STAGES[number]
export type PostFitStage = Exclude<ModelStage, "full">
export const STAGE_LABELS: Record<ModelStage, string> = {
  full: "Full GBM", pruned: "Pruned", banded: "Banded", smoothed: "Smoothed",
}
export interface StageRecipeOperation {
  stage: Exclude<ModelStage, "full">
  parent: ModelStage
  settings: Record<string, unknown>
}
export interface StageSettings {
  schema_version: 1
  enabled: Record<PostFitStage, boolean>
  path: { steps: number; main_effects: boolean; min_rows: number }
  pruning: { fraction: number; tolerance: number; max_tables: number | null }
  banding: { parent: "full" | "pruned"; tolerance: number; deviance_cap: number }
  smoothing: { parent: "full" | "pruned" | "banded"; alpha: number | null; high_order_alpha: number; strength?: number }
  selection?: { relative_tolerance: number; absolute_tolerance: number }
  applied_recipe: StageRecipeOperation[]
  applied_path: StageSettings["path"] | null
}

const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}

/** Return new drafts; committing them is an explicit editor action. */
export function stageConfiguration(config: Record<string, unknown>): { params: Record<string, unknown>; stages: StageSettings } {
  const params = { ...object(config.params) }
  const raw = object(config.stages)
  if (raw.schema_version !== undefined && raw.schema_version !== 1) throw new Error("Unsupported stage settings version. Update Haute before editing this model.")
  const stages: StageSettings = {
    schema_version: 1,
    enabled: { pruned: true, banded: true, smoothed: true },
    path: { steps: 32, main_effects: false, min_rows: 500, ...object(raw.path) },
    pruning: { fraction: .995, tolerance: .001, max_tables: null, ...object(raw.pruning) },
    banding: { parent: "pruned", tolerance: .75, deviance_cap: .001, ...object(raw.banding) },
    smoothing: { parent: "banded", alpha: null, high_order_alpha: 0, strength: 1, ...object(raw.smoothing) },
    selection: { relative_tolerance: .001, absolute_tolerance: 1e-10, ...object(raw.selection) },
    applied_path: raw.applied_path == null ? null : structuredClone(raw.applied_path as StageSettings["path"]),
    applied_recipe: structuredClone((raw.applied_recipe ?? []) as StageRecipeOperation[]),
  }
  const moved: Record<string, [keyof Omit<StageSettings, "schema_version" | "applied_recipe" | "applied_path" | "enabled" | "selection">, string]> = {
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

export function stageMatchesDraft(settings: StageSettings, stage: PostFitStage, snapshot: Record<string, unknown>, path: unknown): boolean {
  const section = stage === "pruned" ? "pruning" : stage === "banded" ? "banding" : "smoothing"
  const { parent = "full", ...draft } = settings[section] as unknown as Record<string, unknown>
  const actual = { ...object(snapshot.settings) }
  if (stage === "smoothed") { actual.strength ??= 1; draft.strength ??= 1 }
  return snapshot.parent === parent && hashConfig({ settings: actual }) === hashConfig({ settings: draft })
    && (stage !== "pruned" || hashConfig({ path: snapshot.path_settings ?? path }) === hashConfig({ path: settings.path }))
}
