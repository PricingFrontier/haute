export interface GeneratedContractValidationError {
  readonly instancePath: string
  readonly schemaPath?: string
  readonly keyword: string
  readonly params: Readonly<Record<string, unknown>>
  readonly message?: string
}

/** The property a required or additional-properties failure names, so the path shows it. */
function namedProperty(error: GeneratedContractValidationError): string | null {
  const value = error.keyword === "required"
    ? error.params.missingProperty
    : error.keyword === "additionalProperties"
      ? error.params.additionalProperty
      : null
  return typeof value === "string" ? value : null
}

export function generatedContractErrorPath(
  error: GeneratedContractValidationError,
): string {
  const named = namedProperty(error)
  const path = named === null
    ? error.instancePath
    : `${error.instancePath}/${named.replaceAll("~", "~0").replaceAll("/", "~1")}`
  return path === "" ? "/" : path
}

export function formatGeneratedContractError(
  contract: string,
  errors: readonly GeneratedContractValidationError[] | null,
): string {
  const error = errors?.[0]
  if (error === undefined) {
    return `${contract}: generated validator rejected the payload without an error`
  }
  return (
    `${contract}: invalid contract at ${generatedContractErrorPath(error)}: `
    + (error.message ?? error.keyword)
  )
}

/** A standalone validator emitted by scripts/generate-api-contracts.mjs. */
export interface GeneratedContractValidator<T> {
  (data: unknown): data is T
  readonly errors: readonly GeneratedContractValidationError[] | null
}

/** Return `value` typed by its generated validator, or throw the contract failure. */
export function expectGeneratedContract<T>(
  contract: string,
  validate: GeneratedContractValidator<T>,
  value: unknown,
): T {
  if (validate(value)) return value
  throw new Error(formatGeneratedContractError(contract, validate.errors))
}

export function findGeneratedContractError(
  errors: readonly GeneratedContractValidationError[] | null,
  predicate: (error: GeneratedContractValidationError) => boolean,
): GeneratedContractValidationError | undefined {
  return errors?.find(predicate)
}
