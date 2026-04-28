declare module "*analyze-bundle-sourcemaps.mjs" {
  export type SourceContributor = {
    source: string
    generatedBytes: number
    originalBytes: number | null
  }

  export type ChunkAnalysis = {
    chunkName: string
    generatedBytes: number
    totalMappedGeneratedBytes: number
    contributors: SourceContributor[]
  }

  export function analyzeSourceMapChunk(args: {
    chunkName: string
    generatedCode: string
    sourceMap: unknown
  }): ChunkAnalysis

  export function analyzeBundleDirectory(assetsDir: string): ChunkAnalysis[]

  export function formatAnalysisReport(
    chunks: ChunkAnalysis[],
    options?: { topN?: number },
  ): string
}
