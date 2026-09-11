/** A response arrived, but its payload does not satisfy the API contract. */
export class ApiResponseValidationError extends Error {
  constructor(message: string, cause: unknown) {
    super(message, { cause })
    this.name = "ApiResponseValidationError"
  }
}
