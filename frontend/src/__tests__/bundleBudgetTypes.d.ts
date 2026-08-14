declare module "*check-bundle-size.mjs" {
  export type JsAsset = {
    name: string
    rawBytes: number
    gzipBytes: number
  }

  export type BundleBudgetsKiB = {
    maxInitialJsGzipKiB: number
    maxTotalJsGzipKiB: number
    maxSingleJsGzipKiB: number
    maxChartVendorJsGzipKiB?: number
  }

  export function parseInitialJsAssetNames(html: string): string[]

  export function parseModulepreloadJsAssetNames(html: string): string[]

  export function formatBundleAssetReadError(
    error: unknown,
    paths?: {
      staticDir?: string
      assetsDir?: string
      indexHtmlPath?: string
    },
  ): string

  export function evaluateBundleBudgets(args: {
    html: string
    jsAssets: JsAsset[]
    budgetsKiB: BundleBudgetsKiB
  }): {
    failures: string[]
    initialAssets: JsAsset[]
    initialGzipBytes: number
    chartVendorAsset: JsAsset | undefined
    jsAssets: JsAsset[]
    largest: JsAsset
    totalGzipBytes: number
  }
}
