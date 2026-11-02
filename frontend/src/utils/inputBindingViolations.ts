import type { PipelineNameViolation } from "../types/pipelineDocument"

function messagesBy(entries: Iterable<[string, string]>): ReadonlyMap<string, string> {
  const messages = new Map<string, string>()
  for (const [name, message] of entries) {
    const previous = messages.get(name)
    messages.set(name, previous === undefined ? message : `${previous} ${message}`)
  }
  return messages
}

/**
 * The violations each frame of request input *nodeId* names, by frame label.
 * A request input is a document-wide singleton, so its node id alone finds it,
 * at the root or in a definition.
 */
export function frameViolationMessages(
  violations: readonly PipelineNameViolation[],
  nodeId: string,
): ReadonlyMap<string, string> {
  return messagesBy(violations.flatMap((violation): [string, string][] => (
    violation.binding?.kind === "frame" && violation.binding.node_id === nodeId
      ? [[violation.binding.handle, violation.message]]
      : []
  )))
}

/** The violations each public input port of definition *definitionId* names, by port name. */
export function portViolationMessages(
  violations: readonly PipelineNameViolation[],
  definitionId: string,
): ReadonlyMap<string, string> {
  return messagesBy(violations.flatMap((violation): [string, string][] => (
    violation.binding?.kind === "submodel_input" && violation.binding.submodel === definitionId
      ? [[violation.binding.port, violation.message]]
      : []
  )))
}
