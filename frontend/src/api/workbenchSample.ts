/**
 * The sample the workbench view holds, saved or not (specs/workbench, Live sample): the
 * workbench store publishes it (`useWorkbenchStore.noteSample`) whenever the form store's
 * sample changes, and it is null until the form is read into the view. Each of the pipeline
 * editor's reads of the pipeline's data sends it as `workbench_sample`
 * (`workbenchSampleField`), so the Workbench Input previews what the sheets hold over the
 * saved form, with no save.
 */
import type { FormSpec } from "./types"

export type WorkbenchSample = FormSpec["sample"]

let sample: WorkbenchSample | null = null

export function setWorkbenchSample(next: WorkbenchSample | null): void {
  sample = next
}

export function workbenchSample(): WorkbenchSample | null {
  return sample
}

/** The request field a read of the pipeline's data spreads into its body: none while no sample is held. */
export function workbenchSampleField(): { workbench_sample?: WorkbenchSample } {
  return sample === null ? {} : { workbench_sample: sample }
}
