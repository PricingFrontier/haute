import { Handle, Position } from "@xyflow/react"
import type { SubmodelBoundaryPort } from "../types/node"
import {
  DEFAULT_TARGET_HANDLE,
  INPUT_ORIGIN_HANDLE_CLASS,
  OUTPUT_ORIGIN_HANDLE_CLASS,
} from "../utils/flowHandles"

interface DefaultInputPortProps {
  accent: string
  handleId?: string
  handleTestId?: string
  edgeAnchorIds?: readonly string[]
  isConnectable?: boolean
}

export function DefaultInputPort({
  accent,
  handleId = DEFAULT_TARGET_HANDLE,
  handleTestId,
  edgeAnchorIds = [],
  isConnectable,
}: DefaultInputPortProps) {
  return (
    <>
      <span
        className="mr-2 shrink-0 text-left text-[11px] leading-tight"
        style={{ color: "var(--text-muted)" }}
      >
        inputs
      </span>
      <Handle
        id={handleId}
        className={INPUT_ORIGIN_HANDLE_CLASS}
        type="target"
        position={Position.Left}
        isConnectable={isConnectable}
        style={{ top: "50%", color: accent, zIndex: 1 }}
        {...(handleTestId ? { "data-testid": handleTestId } : {})}
      />
      {edgeAnchorIds.map((anchorId) => (
        <Handle
          key={anchorId}
          id={anchorId}
          className="submodel-input-edge-anchor"
          type="target"
          position={Position.Left}
          isConnectable={false}
          aria-hidden="true"
          style={{
            top: "50%",
            opacity: 0,
            pointerEvents: "none",
            zIndex: 0,
          }}
        />
      ))}
    </>
  )
}

interface DefaultInputPortRowProps extends DefaultInputPortProps {
  rowTestId: string
}

export function DefaultInputPortRow({
  accent,
  handleId,
  rowTestId,
  handleTestId,
  edgeAnchorIds,
  isConnectable,
}: DefaultInputPortRowProps) {
  return (
    <div
      data-testid={rowTestId}
      className="relative flex min-w-0 items-center justify-start py-0.5 pl-3"
      style={{ marginLeft: "-12px" }}
    >
      <DefaultInputPort
        accent={accent}
        handleId={handleId}
        handleTestId={handleTestId}
        edgeAnchorIds={edgeAnchorIds}
        isConnectable={isConnectable}
      />
    </div>
  )
}

interface FramePortRowsProps {
  ports: readonly SubmodelBoundaryPort[]
  direction: "source" | "target"
  accent: string
  testIdPrefix: string
  isConnectableEnd?: boolean
  handleTestId?: (port: SubmodelBoundaryPort, index: number) => string
  /** Optional default target rendered at the left of the first source row. */
  firstRowInput?: Omit<DefaultInputPortProps, "accent">
}

export default function FramePortRows({
  ports,
  direction,
  accent,
  testIdPrefix,
  isConnectableEnd,
  handleTestId,
  firstRowInput,
}: FramePortRowsProps) {
  const isSource = direction === "source"

  return (
    <div className="flex flex-col gap-0.5">
      {ports.map((port, index) => {
        const sharesFirstInput = isSource && index === 0 && firstRowInput !== undefined
        return (
          <div
            key={port.id}
            data-testid={`${testIdPrefix}-frame-row-${port.id}`}
            className={
              isSource
                ? `relative flex min-w-0 items-center justify-end py-0.5 pr-3${sharesFirstInput ? " pl-3" : ""}`
                : "relative flex min-w-0 items-center justify-start py-0.5 pl-3"
            }
            style={isSource
              ? {
                  marginRight: "-12px",
                  ...(sharesFirstInput ? { marginLeft: "-12px" } : {}),
                }
              : { marginLeft: "-12px" }}
          >
            {sharesFirstInput && <DefaultInputPort accent={accent} {...firstRowInput} />}
            <span
              data-testid={`${testIdPrefix}-body-label-${port.id}`}
              className={`min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-pre font-semibold text-[13px] leading-tight ${isSource ? "text-right" : "text-left"}`}
              style={{ color: "var(--text-primary)", whiteSpace: "pre" }}
              title={port.label}
            >
              {port.label}
            </span>
            <Handle
              id={port.id}
              className={
                isSource
                  ? OUTPUT_ORIGIN_HANDLE_CLASS
                  : INPUT_ORIGIN_HANDLE_CLASS
              }
              type={direction}
              position={isSource ? Position.Right : Position.Left}
              isConnectableEnd={isSource ? isConnectableEnd : undefined}
              style={{
                top: "50%",
                color: accent,
              }}
              {...(handleTestId
                ? { "data-testid": handleTestId(port, index) }
                : {})}
            />
          </div>
        )
      })}
    </div>
  )
}
