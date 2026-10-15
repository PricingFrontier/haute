/**
 * The project's workbench (specs/workbench): whether it is enabled in haute.toml
 * (GET /api/workbench), its tables, sample and response tables as its saved form defines
 * them now (GET /api/workbench/tables) or as an unsaved form would (POST
 * /api/workbench/tables), and the form itself with the file's revision
 * (GET /api/workbench/form), saved against that revision and captured on the save ledger
 * (PUT /api/workbench/form); a params file's header and a column's values, what allowed
 * values are read from (GET /api/workbench/params); and the test quotes an analyst keeps in
 * Preview (/api/workbench/test-quotes), each write captured on the ledger as the form's are.
 */

import { expectGeneratedContract } from "../types/generatedContractValidation"
import { request } from "./client"
import type {
  FormSpec,
  WorkbenchFormResponse,
  WorkbenchFormSaveResponse,
  WorkbenchParamFileResponse,
  WorkbenchStatusResponse,
  WorkbenchTablesResponse,
  WorkbenchTestQuoteCompareResponse,
  WorkbenchTestQuoteOpenResponse,
  WorkbenchTestQuotesResponse,
  WorkbenchTestQuoteWriteResponse,
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

/**
 * A params file's header, with the distinct values of `column` when one is named: what a
 * column's allowed values are read from. A file or column that cannot give them answers 409
 * with why.
 */
export async function fetchParamFile(file: string, column?: string): Promise<WorkbenchParamFileResponse> {
  const query = new URLSearchParams({ file, ...(column === undefined ? {} : { column }) })
  const data = await request<unknown>(`/api/workbench/params?${query}`)
  return expectGeneratedContract(
    "WorkbenchParamFileResponse",
    (await workbenchValidators()).validateWorkbenchParamFileResponse,
    data,
  )
}

/** Values typed into the sheets: by table id, rows keyed by column id, as typed. */
export type SheetQuote = Record<string, Record<string, string | boolean>[]>
/** Priced output tables: each table's rows by its name, as Preview's pricing gives them. */
export type PricedTables = Record<string, Record<string, unknown>[]>

/** One place a priced response differs from the accepted one; a value may be any JSON. */
export interface TestQuoteChange {
  path: string
  kind: "changed" | "added" | "removed"
  accepted: unknown
  priced: unknown
}

const JSON_HEADERS = { "Content-Type": "application/json" }

export async function fetchTestQuotes(): Promise<WorkbenchTestQuotesResponse> {
  const data = await request<unknown>("/api/workbench/test-quotes")
  return expectGeneratedContract(
    "WorkbenchTestQuotesResponse",
    (await workbenchValidators()).validateWorkbenchTestQuotesResponse,
    data,
  )
}

/** The branch the test quotes are on now, which a save names: the file is not opened. */
export async function fetchTestQuotesBranch(): Promise<string | null> {
  const data = await request<unknown>("/api/workbench/test-quotes/branch")
  const named = expectGeneratedContract(
    "WorkbenchTestQuotesBranchResponse",
    (await workbenchValidators()).validateWorkbenchTestQuotesBranchResponse,
    data,
  )
  return named.branch ?? null
}

async function writeResponse(data: unknown): Promise<WorkbenchTestQuoteWriteResponse> {
  return expectGeneratedContract(
    "WorkbenchTestQuoteWriteResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteWriteResponse,
    data,
  )
}

/** Save the quote on the sheets as a new test quote, with its pricing's tables in full. */
/**
 * Save the quote as a new test quote on `branch`, the branch the save started on as
 * `fetchTestQuotesBranch` named it: a branch checked out since answers 409 and nothing is
 * written.
 */
export async function saveTestQuote(
  name: string, form: FormSpec, values: SheetQuote, tables: PricedTables, branch: string | null,
): Promise<WorkbenchTestQuoteWriteResponse> {
  const data = await request<unknown>("/api/workbench/test-quotes", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ name, form, values, tables, branch }),
    // One attempt: a retry of a save that landed would save the quote twice.
    retry: { maxRetries: 0 },
  })
  return writeResponse(data)
}

/** A test quote's newest revision, its values carried onto the form as the view holds it. */
export async function openTestQuote(id: string, form: FormSpec): Promise<WorkbenchTestQuoteOpenResponse> {
  const data = await request<unknown>(`/api/workbench/test-quotes/${encodeURIComponent(id)}/open`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ form }),
  })
  return expectGeneratedContract(
    "WorkbenchTestQuoteOpenResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteOpenResponse,
    data,
  )
}

/** Every place a run's priced tables differ from what revision `number` accepted. */
/**
 * Compare a run's response and request with revision `number`, the one `digest` names: a
 * revision `number` saved apart since it was opened (another branch's) answers 409.
 */
export async function compareTestQuote(
  id: string, form: FormSpec, values: SheetQuote, tables: PricedTables, number: number, digest: string,
): Promise<{ number: number; digest: string; changes: TestQuoteChange[] }> {
  const data = await request<unknown>(`/api/workbench/test-quotes/${encodeURIComponent(id)}/compare`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ form, values, tables, number, revision_digest: digest }),
  })
  const compared: WorkbenchTestQuoteCompareResponse = expectGeneratedContract(
    "WorkbenchTestQuoteCompareResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteCompareResponse,
    data,
  )
  // The generated type reads an any-valued field as an object; a value may be any JSON.
  return {
    number: compared.number,
    digest: compared.revision_digest,
    changes: compared.changes as unknown as TestQuoteChange[],
  }
}

/**
 * Accept a run's response as the test quote's revision after `after`, the one `digest` names.
 * A quote saved since, or a revision `after` saved apart on another branch, answers 409 with
 * a `stale_quote_revision` detail and nothing is written.
 */
export async function acceptTestQuote(
  id: string, after: number, digest: string, form: FormSpec, values: SheetQuote, tables: PricedTables,
): Promise<WorkbenchTestQuoteWriteResponse> {
  const data = await request<unknown>(`/api/workbench/test-quotes/${encodeURIComponent(id)}/accept`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ after, revision_digest: digest, form, values, tables }),
    retry: { maxRetries: 0 },
  })
  return writeResponse(data)
}

export async function deleteTestQuote(id: string): Promise<WorkbenchTestQuoteWriteResponse> {
  const data = await request<unknown>(`/api/workbench/test-quotes/${encodeURIComponent(id)}`, {
    method: "DELETE",
    retry: { maxRetries: 0 },
  })
  return writeResponse(data)
}
