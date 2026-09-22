import { useState } from "react"
import type { FeatureBrowserProps, FeatureItem } from "./FeatureBrowser"

export type SharedFeatureBrowser = Required<
  Pick<FeatureBrowserProps, "features" | "selected" | "onSelect" | "search" | "onSearch">
>

/** Standalone panes own their selection; the result workspace supplies shared state. */
export function useDiagnosticFeature(
  features: FeatureItem[],
  shared?: SharedFeatureBrowser,
): SharedFeatureBrowser {
  const [selection, setSelection] = useState<string | null>(null)
  const [search, setSearch] = useState("")
  return (
    shared ?? {
      features,
      selected: features.some((item) => item.feature === selection)
        ? selection
        : (features[0]?.feature ?? null),
      onSelect: setSelection,
      search,
      onSearch: setSearch,
    }
  )
}
