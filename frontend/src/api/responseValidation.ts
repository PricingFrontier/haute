/** A response arrived, but its payload does not satisfy the API contract. */
export class ApiResponseValidationError extends Error {
  constructor(message: string, cause: unknown) {
    super(message, { cause })
    this.name = "ApiResponseValidationError"
  }
}

/** Run a response parser, reporting its failure as a contract violation. */
export function validateApiResponse<T>(context: string, parse: () => T): T {
  try {
    return parse()
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    throw new ApiResponseValidationError(`${context}: ${detail}`, error)
  }
}
