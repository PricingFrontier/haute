import { Handle, Position } from "@xyflow/react"
import type { SubmodelBoundaryPort } from "../types/node"

interface FramePortRowsProps {
  ports: readonly SubmodelBoundaryPort[]
  direction: "source" | "target"
  accent: string
  testIdPrefix: string
  isConnectableEnd?: boolean
  handleTestId?: (port: SubmodelBoundaryPort, index: number) => string
}

export default function FramePortRows({
  ports,
  direction,
  accent,
  testIdPrefix,
  isConnectableEnd,
  handleTestId,
}: FramePortRowsProps) {
  const isSource = direction === "source"

  return (
    <div className="flex flex-col gap-0.5">
      {ports.map((port, index) => (
        <div
          key={port.id}
          data-testid={`${testIdPrefix}-frame-row-${port.id}`}
          className={
            isSource
              ? "relative flex min-w-0 items-center justify-end py-0.5 pr-3"
              : "relative flex min-w-0 items-center justify-start py-0.5 pl-3"
          }
          style={isSource ? { marginRight: "-12px" } : { marginLeft: "-12px" }}
        >
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
            type={direction}
            position={isSource ? Position.Right : Position.Left}
            isConnectableEnd={isSource ? isConnectableEnd : undefined}
            style={{ top: "50%", background: accent }}
            {...(handleTestId
              ? { "data-testid": handleTestId(port, index) }
              : {})}
          />
        </div>
      ))}
    </div>
  )
}
