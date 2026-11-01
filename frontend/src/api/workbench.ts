/**
 * The project's workbench (specs/workbench): whether it is enabled in haute.toml
 * (GET /api/workbench), its tables, sample and response tables, with the columns the quote
 * store keeps in each table that the form does not, as its saved form defines them now
 * (GET /api/workbench/tables) or as an unsaved form would (POST /api/workbench/tables), and
 * the form itself with the file's revision
 * (GET /api/workbench/form), saved against that revision and captured on the save ledger
 * (PUT /api/workbench/form); a params file's header and a column's values, what allowed
 * values are read from (GET /api/workbench/params); and the SQL that brings each hosted
 * environment's tables to a form (POST /api/workbench/store-sql); and the test quotes
 * (specs/workbench, Test quotes): listed, saved, opened, run, accepted and deleted
 * under /api/workbench/test-quotes, none of them with a deadline of the panel's own,
 * so a write is never reported failed while it is under way.
 */

import { expectGeneratedContract } from "../types/generatedContractValidation"
import { request } from "./client"
import type {
  FormSpec,
  WorkbenchFormResponse,
  WorkbenchFormSaveResponse,
  WorkbenchParamFileResponse,
  WorkbenchCatalogueTable,
  WorkbenchStatusResponse,
  WorkbenchStoreCatalogueResponse,
  WorkbenchStoreCompareResponse,
  WorkbenchStoreRename,
  WorkbenchStoreSqlResponse,
  WorkbenchTablesResponse,
  WorkbenchTestQuoteOpenResponse,
  WorkbenchTestQuoteRunResponse,
  WorkbenchTestQuotesAcceptResponse,
  WorkbenchTestQuotesResponse,
  WorkbenchTestQuoteTarget,
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

/**
 * The SQL that brings each environment the project declares to `form`, as the view
 * holds it, from the form the default branch holds (`branch`) or from none.
 */
export async function fetchStoreSql(form: FormSpec, start: "branch" | "none"): Promise<WorkbenchStoreSqlResponse> {
  const data = await request<unknown>("/api/workbench/store-sql", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ form, start }),
  })
  return expectGeneratedContract(
    "WorkbenchStoreSqlResponse",
    (await workbenchValidators()).validateWorkbenchStoreSqlResponse,
    data,
  )
}

/**
 * The tables `environment`'s database holds, read through its connection in connections.toml
 * under the analyst's own sign-in, with the quotes holding values in each table and column.
 */
export async function fetchStoreCatalogue(environment: string): Promise<WorkbenchStoreCatalogueResponse> {
  const data = await request<unknown>("/api/workbench/store-catalogue", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ environment }),
  })
  return expectGeneratedContract(
    "WorkbenchStoreCatalogueResponse",
    (await workbenchValidators()).validateWorkbenchStoreCatalogueResponse,
    data,
  )
}

/**
 * `form`, as the view holds it, compared with `tables`, what `environment`'s database holds,
 * the columns in `renames` paired as renames: its differences, extras and the SQL from them.
 */
export async function fetchStoreCompare(
  form: FormSpec,
  environment: string,
  tables: WorkbenchCatalogueTable[],
  renames: WorkbenchStoreRename[],
): Promise<WorkbenchStoreCompareResponse> {
  const data = await request<unknown>("/api/workbench/store-compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ form, environment, tables, renames }),
  })
  return expectGeneratedContract(
    "WorkbenchStoreCompareResponse",
    (await workbenchValidators()).validateWorkbenchStoreCompareResponse,
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
export async function saveWorkbenchForm(form: FormSpec, baseRevision: string): Promise<WorkbenchFormSaveResponse> {
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


/** The test quotes' calls wait on the server and are never retried: a write that landed
 * but whose answer was slow is not reported failed, nor made twice. */
const TEST_QUOTE_CALL = { timeout: null, retry: { maxRetries: 0 } } as const

function testQuotePath(file: string, action = ""): string {
  return `/api/workbench/test-quotes/${encodeURIComponent(file)}${action}`
}

/** The test-quote folder's files by name, and the branch they were read on. */
export async function fetchTestQuotes(): Promise<WorkbenchTestQuotesResponse> {
  const data = await request<unknown>("/api/workbench/test-quotes", TEST_QUOTE_CALL)
  return expectGeneratedContract(
    "WorkbenchTestQuotesResponse",
    (await workbenchValidators()).validateWorkbenchTestQuotesResponse,
    data,
  )
}

/**
 * Save the sample as saved: under a new `name`, or over the test quote `replace` names; on
 * `branch`, the form folder's `formRevision` the panel saved.
 */
export async function saveTestQuote(args: {
  name?: string
  replace?: WorkbenchTestQuoteTarget
  branch: string
  formRevision: string
}): Promise<WorkbenchTestQuoteWriteResponse> {
  const data = await request<unknown>("/api/workbench/test-quotes", {
    ...TEST_QUOTE_CALL,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: args.name ?? null,
      replace: args.replace ?? null,
      branch: args.branch,
      form_revision: args.formRevision,
    }),
  })
  return expectGeneratedContract(
    "WorkbenchTestQuoteWriteResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteWriteResponse,
    data,
  )
}

/** A test quote's values put back onto `form`, as the view holds it, and what was not. */
export async function openTestQuote(file: string, form: FormSpec): Promise<WorkbenchTestQuoteOpenResponse> {
  const data = await request<unknown>(testQuotePath(file, "/open"), {
    ...TEST_QUOTE_CALL,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ form }),
  })
  return expectGeneratedContract(
    "WorkbenchTestQuoteOpenResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteOpenResponse,
    data,
  )
}

/** A test quote priced on the project as saved and compared with its expected answer. */
export async function runTestQuote(file: string): Promise<WorkbenchTestQuoteRunResponse> {
  const data = await request<unknown>(testQuotePath(file, "/run"), { ...TEST_QUOTE_CALL, method: "POST" })
  return expectGeneratedContract(
    "WorkbenchTestQuoteRunResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteRunResponse,
    data,
  )
}

/** Accept the new answers of `quotes`, priced again on the server on the run's `basis`. */
export async function acceptTestQuotes(args: {
  branch: string
  basis: string
  quotes: WorkbenchTestQuoteTarget[]
}): Promise<WorkbenchTestQuotesAcceptResponse> {
  const data = await request<unknown>("/api/workbench/test-quotes/accept", {
    ...TEST_QUOTE_CALL,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  })
  return expectGeneratedContract(
    "WorkbenchTestQuotesAcceptResponse",
    (await workbenchValidators()).validateWorkbenchTestQuotesAcceptResponse,
    data,
  )
}

/** Delete a test quote, while it is still `revision` on `branch`. */
export async function deleteTestQuote(file: string, revision: string, branch: string): Promise<WorkbenchTestQuoteWriteResponse> {
  const query = new URLSearchParams({ revision, branch })
  const data = await request<unknown>(`${testQuotePath(file)}?${query}`, { ...TEST_QUOTE_CALL, method: "DELETE" })
  return expectGeneratedContract(
    "WorkbenchTestQuoteWriteResponse",
    (await workbenchValidators()).validateWorkbenchTestQuoteWriteResponse,
    data,
  )
}
