/**
 * The project's workbench (specs/workbench): whether it is enabled in haute.toml
 * (GET /api/workbench), its tables, sample and response tables as its saved form defines
 * them now (GET /api/workbench/tables) or as an unsaved form would (POST
 * /api/workbench/tables), and the form itself with the file's revision
 * (GET /api/workbench/form), saved against that revision and captured on the save ledger
 * (PUT /api/workbench/form).
 */

import { expectGeneratedContract } from "../types/generatedContractValidation"
import { request } from "./client"
import type {
  FormSpec,
  WorkbenchFormResponse,
  WorkbenchFormSaveResponse,
  WorkbenchStatusResponse,
  WorkbenchTablesResponse,
} from "./types"

// Loads with the first response, so it never reaches the initial bundle.
const workbenchValidators = () => import("../generated/api-contracts.workbench.validators.mjs")

export async function fetchWorkbenchStatus(): Promise<WorkbenchStatusResponse> {
  const data = await request<unknown>("/api/workbench")
  return expectGeneratedContract(
    "WorkbenchStatusResponse",
    (await workbenchValidators()).validateWorkbenchStatusResponse,
    data,
  )
}

export async function fetchWorkbenchTables(): Promise<WorkbenchTablesResponse> {
  const data = await request<unknown>("/api/workbench/tables")
  return expectGeneratedContract(
    "WorkbenchTablesResponse",
    (await workbenchValidators()).validateWorkbenchTablesResponse,
    data,
  )
}

/** The tables, sample and response tables of a form as the view holds it, saved or not. */
export async function fetchWorkbenchFormTables(form: FormSpec): Promise<WorkbenchTablesResponse> {
  const data = await request<unknown>("/api/workbench/tables", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ form }),
  })
  return expectGeneratedContract(
    "WorkbenchTablesResponse",
    (await workbenchValidators()).validateWorkbenchTablesResponse,
    data,
  )
}

export async function fetchWorkbenchForm(): Promise<WorkbenchFormResponse> {
  const data = await request<unknown>("/api/workbench/form")
  return expectGeneratedContract(
    "WorkbenchFormResponse",
    (await workbenchValidators()).validateWorkbenchFormResponse,
    data,
  )
}

/**
 * Write the form, quoting the revision it was read at (null for a form never saved). A
 * file that changed since answers 409 with a `stale_document_revision` detail and is
 * left as it is. A save that lands answers the form with its new revision and what its
 * capture on the save ledger gave, as the pipeline's save does.
 */
export async function saveWorkbenchForm(form: FormSpec, baseRevision: string | null): Promise<WorkbenchFormSaveResponse> {
  const data = await request<unknown>("/api/workbench/form", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ form, base_revision: baseRevision }),
    // One attempt, as the pipeline's save makes: a retry of a save that landed but
    // whose answer was lost would be refused as stale.
    retry: { maxRetries: 0 },
  })
  return expectGeneratedContract(
    "WorkbenchFormSaveResponse",
    (await workbenchValidators()).validateWorkbenchFormSaveResponse,
    data,
  )
}
