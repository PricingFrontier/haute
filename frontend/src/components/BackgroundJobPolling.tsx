import { memo } from "react"
import useBackgroundJobs from "../hooks/useBackgroundJobs"

function BackgroundJobPolling() {
  useBackgroundJobs()
  return null
}

export default memo(BackgroundJobPolling)
