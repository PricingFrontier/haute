export interface GeneratedContractValidationError {
  readonly instancePath: string
  readonly schemaPath?: string
  readonly keyword: string
  readonly params: Readonly<Record<string, unknown>>
  readonly message?: string
}

function missingProperty(error: GeneratedContractValidationError): string | null {
  const value = error.params.missingProperty
  return error.keyword === "required" && typeof value === "string" ? value : null
}

export function generatedContractErrorPath(
  error: GeneratedContractValidationError,
): string {
  const missing = missingProperty(error)
  const path = missing === null
    ? error.instancePath
    : `${error.instancePath}/${missing.replaceAll("~", "~0").replaceAll("/", "~1")}`
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

export function findGeneratedContractError(
  errors: readonly GeneratedContractValidationError[] | null,
  predicate: (error: GeneratedContractValidationError) => boolean,
): GeneratedContractValidationError | undefined {
  return errors?.find(predicate)
}
