/**
 * An Import's route in the builder (POST /api/workbench/import) and what an Import's call
 * shares with the app's (specs/workbench, Running an Import): apart from the builder's other
 * calls, so it loads with the workbench's views alone.
 */

import type { SampleRow } from "../utils/workbenchForm"
import { expectGeneratedContract } from "../types/generatedContractValidation"
import { post } from "./client"
import type { FormSpec, WorkbenchImportResponse } from "./types"

// An Import's answer, which the app's page reads too: a module of its own.
const importValidators = () => import("../generated/api-contracts.workbench-import.validators.mjs")

/** What runs an Import on what a sheet holds, by the Import's id, given the file picked for a file Import (null for any other). */
export type RowsImporter = (componentId: string, file: File | null) => Promise<WorkbenchImportResponse>

/** A source is given its own time to answer (30 seconds for an API); the browser waits a little longer. */
export const IMPORT_TIMEOUT_MS = 90_000

/** `file` as base64 text, as an Import sends a file. */
export async function fileText(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let text = ""
  // In pieces: a whole workbook spread into one call overflows the stack.
  for (let at = 0; at < bytes.length; at += 0x8000) text += String.fromCharCode(...bytes.subarray(at, at + 0x8000))
  return btoa(text)
}

/** An Import's answer, checked against its contract. */
export async function importedRows(data: unknown): Promise<WorkbenchImportResponse> {
  return expectGeneratedContract("WorkbenchImportResponse", (await importValidators()).validateWorkbenchImportResponse, data)
}

/** The rows the Import `componentId` of `form` gives its table, run in Preview on the sample's `values`. */
export async function importWorkbenchRows(
  form: FormSpec,
  componentId: string,
  values: Readonly<Record<string, readonly SampleRow[]>>,
  file: File | null,
): Promise<WorkbenchImportResponse> {
  // The form whole, as the Export's request carries it: no generated request type.
  const body = { form, component: componentId, values, file: file === null ? null : await fileText(file) }
  return importedRows(await post<unknown>("/api/workbench/import", body, { timeout: IMPORT_TIMEOUT_MS }))
}
