import { describe, expect, it } from "vitest"
import { ApiError } from "../client"
import { apiErrorCode, apiErrorMessage } from "../errors"

describe("apiErrorCode", () => {
  it("reads the code from a structured detail", () => {
    const error = new ApiError("HTTP 409", 409, undefined, undefined, {
      error_code: "model_file_exists",
      message: "Model file already exists: models/freq.cbm",
    })
    expect(apiErrorCode(error)).toBe("model_file_exists")
  })

  it("is null for a string detail, an empty code, or a non-HTTP error", () => {
    expect(apiErrorCode(new ApiError("HTTP 400", 400, "bad", undefined, "bad"))).toBeNull()
    expect(apiErrorCode(new ApiError("HTTP 400", 400, undefined, undefined, { error_code: "" }))).toBeNull()
    expect(apiErrorCode(new Error("boom"))).toBeNull()
  })
})

describe("apiErrorMessage", () => {
  it("prefers a structured detail's message", () => {
    const error = new ApiError("HTTP 502", 502, undefined, undefined, {
      error_code: "mlflow_connectivity",
      message: "Could not reach the MLflow tracking server.",
    })
    expect(apiErrorMessage(error, "fallback")).toBe("Could not reach the MLflow tracking server.")
  })

  it("uses a string detail, whether or not the raw body was kept", () => {
    expect(apiErrorMessage(new ApiError("HTTP 400", 400, "Pick a folder.", undefined, "Pick a folder."), "x"))
      .toBe("Pick a folder.")
    expect(apiErrorMessage(new ApiError("HTTP 400", 400, "Pick a folder."), "x")).toBe("Pick a folder.")
  })

  it("never shows the bare HTTP status for an error without a detail", () => {
    expect(apiErrorMessage(new ApiError("HTTP 500", 500), "Logging failed.")).toBe("Logging failed.")
  })

  it("uses a thrown error's own message, else the fallback", () => {
    expect(apiErrorMessage(new Error("Network error"), "x")).toBe("Network error")
    expect(apiErrorMessage("weird", "Something failed.")).toBe("Something failed.")
  })

  it("without a fallback, uses the status message or the value's string form", () => {
    expect(apiErrorMessage(new ApiError("HTTP 500", 500))).toBe("HTTP 500")
    expect(apiErrorMessage(new ApiError("HTTP 400", 400, "Pick a folder."))).toBe("Pick a folder.")
    expect(apiErrorMessage("weird")).toBe("weird")
  })

  it("reads an execution detail before the stringified detail", () => {
    const error = new ApiError("HTTP 507", 507, "{\"reason\":\"memory\"}", undefined, { reason: "Not enough memory." })
    expect(apiErrorMessage(error)).toBe("Not enough memory.")
  })
})
