import { NODE_TYPES, NODE_TYPE_META, type NodeTypeValue } from "../utils/nodeTypes"

type NodeTypeIconProps = {
  nodeType?: string | null
  size?: number
  className?: string
}

function isKnownNodeType(nodeType: string | null | undefined): nodeType is NodeTypeValue {
  return typeof nodeType === "string" && Object.prototype.hasOwnProperty.call(NODE_TYPE_META, nodeType)
}

function getNodeTypeMeta(nodeType: string | null | undefined) {
  return NODE_TYPE_META[isKnownNodeType(nodeType) ? nodeType : NODE_TYPES.POLARS]
}

export default function NodeTypeIcon({ nodeType, size = 14, className }: NodeTypeIconProps) {
  const meta = getNodeTypeMeta(nodeType)
  const Icon = meta.icon

  return <Icon size={size} className={className} style={{ color: meta.color }} />
}
