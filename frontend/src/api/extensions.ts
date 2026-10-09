/**
 * Installed extensions (GET /api/extensions): packages that add a view beside
 * the pipeline editor, such as Obverse, and the Quote Input's tables when one
 * supplies them (GET /api/quote-tables). See specs/extensions.
 */

import { expectGeneratedContract } from "../types/generatedContractValidation"
import { request } from "./client"
import type { ExtensionsResponse, QuoteTablesResponse } from "./types"

// Loads with the first response, so it never reaches the initial bundle.
const extensionsValidators = () => import("../generated/api-contracts.extensions.validators.mjs")

export async function fetchExtensions(): Promise<ExtensionsResponse> {
  const data = await request<unknown>("/api/extensions")
  return expectGeneratedContract(
    "ExtensionsResponse",
    (await extensionsValidators()).validateExtensionsResponse,
    data,
  )
}

export async function fetchQuoteTables(): Promise<QuoteTablesResponse> {
  const data = await request<unknown>("/api/quote-tables")
  return expectGeneratedContract(
    "QuoteTablesResponse",
    (await extensionsValidators()).validateQuoteTablesResponse,
    data,
  )
}
