/**
 * The underwriter app's routes (specs/workbench): the release it serves with who the host
 * signed in (GET /api/app/release), the release's form with its revision
 * (GET /api/app/form), and a quote priced through the release's pipeline
 * (POST /api/app/price), which a page drawn for another revision of the form is refused.
 */

import type { SampleRow } from "../utils/workbenchForm"
import { expectGeneratedContract } from "../types/generatedContractValidation"
import { post, request } from "./client"
import type { WorkbenchAppFormResponse, WorkbenchAppPriceResponse, WorkbenchAppReleaseResponse } from "./types"

const workbenchValidators = () => import("../generated/api-contracts.workbench.validators.mjs")

/** The app answers a pricing within its own time limit; the browser waits a little longer. */
const PRICE_TIMEOUT_MS = 90_000

export async function fetchAppRelease(): Promise<WorkbenchAppReleaseResponse> {
  const data = await request<unknown>("/api/app/release")
  return expectGeneratedContract(
    "WorkbenchAppReleaseResponse",
    (await workbenchValidators()).validateWorkbenchAppReleaseResponse,
    data,
  )
}

export async function fetchAppForm(): Promise<WorkbenchAppFormResponse> {
  const data = await request<unknown>("/api/app/form")
  return expectGeneratedContract(
    "WorkbenchAppFormResponse",
    (await workbenchValidators()).validateWorkbenchAppFormResponse,
    data,
  )
}

/** The quote typed into the sheets drawn from form revision `revision`, priced. */
export async function priceAppQuote(
  revision: string,
  values: Record<string, SampleRow[]>,
): Promise<WorkbenchAppPriceResponse> {
  const data = await post<unknown>("/api/app/price", { form_revision: revision, values }, { timeout: PRICE_TIMEOUT_MS })
  return expectGeneratedContract(
    "WorkbenchAppPriceResponse",
    (await workbenchValidators()).validateWorkbenchAppPriceResponse,
    data,
  )
}
