declare module "*check-ui-dependencies.mjs" {
  export type SourceFile = {
    path: string
    source: string
  }

  export type JsAsset = {
    name: string
    gzipBytes: number
  }

  export function auditUiDependencyImports(files: SourceFile[]): {
    failures: string[]
  }

  export function evaluateVendorUiBudget(args: {
    jsAssets: JsAsset[]
    maxVendorUiGzipKiB: number
  }): {
    vendorUiAsset: JsAsset
    failures: string[]
  }
}
