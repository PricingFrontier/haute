import { useEffect, useState } from "react"
import { JobPollingController, type JobPollingConfig } from "./jobPollingController"

export type UseJobPollingConfig<TJob, TStatus> = JobPollingConfig<TJob, TStatus>

/** React adapter for the framework-free background job polling controller. */
export default function useJobPolling<TJob, TStatus>(config: UseJobPollingConfig<TJob, TStatus>): void {
  const [controller] = useState(() => new JobPollingController(config))

  useEffect(() => {
    controller.updateConfig(config)
    controller.reconcile()
  }, [config, controller])

  useEffect(() => () => controller.dispose(), [controller])
}
