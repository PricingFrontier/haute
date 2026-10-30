/**
 * The journeys' project as a workbench project (specs/workbench): switched on in haute.toml,
 * its form written to the pipeline folder's workbench/ file by file, and the scaffold's Quote
 * Input and Quote Response replaced with a workbench pipeline saved through the builder.
 */
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { expect, type Page } from "@playwright/test"

import { e2eProjectRoot } from "./projectIsolation"

/** The form's folder: the pipeline folder's workbench/. */
export const FORM_FOLDER = resolve(e2eProjectRoot, "rating", "workbench")

/**
 * The form as a pricing team would start it, file by file, each saying only what is set: a
 * policy table in, a pricing table out, and one blank sheet.
 */
export const FORM_FILES: Record<string, unknown> = {
  "form.json": { version: 1, name: "haute-e2e", schema: ["quote_details", "policy", "pricing"], sheets: ["sheet_1"] },
  "schema/quote_details.json": { columns: [] },
  "schema/policy.json": {
    id: "t_policy",
    role: "input",
    rows: "one",
    columns: [
      { id: "c_limit", name: "limit", type: "int", required: true },
      { id: "c_region", name: "region", type: "str", options: ["north", "south"] },
    ],
  },
  "schema/pricing.json": { id: "t_pricing", role: "output", rows: "one", columns: [{ id: "c_premium", name: "premium", type: "float" }] },
  "sheets/sheet_1.json": { id: "page_1", title: "Sheet 1", sections: [{ id: "section_1", title: "Section 1", components: [] }] },
  "sheets/sheet_1.layout.json": { width: 1280, sections: { section_1: { height: 560, components: {} } } },
}

export const readFormFile = (path: string) => JSON.parse(readFileSync(resolve(FORM_FOLDER, path), "utf8"))

/** Write each of `files` to the form folder, by its path there, as a save writes them. */
export function writeFormFiles(files: Record<string, unknown>): void {
  for (const [path, content] of Object.entries(files)) {
    const file = resolve(FORM_FOLDER, path)
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, JSON.stringify(content, null, 2) + "\n")
  }
}

/** Switch the project's workbench on and write its form's files, as `haute init --workbench` and a save would. */
export function enableWorkbench(files: Record<string, unknown> = FORM_FILES): void {
  appendFileSync(resolve(e2eProjectRoot, "haute.toml"), "\n[workbench]\nenabled = true\n")
  writeFormFiles(files)
}

/**
 * Replace the scaffold's Quote Input and Quote Response with a workbench pipeline: a
 * Workbench Input whose policy table a Transform prices, filling a Workbench Output's
 * pricing table. Saved through the API, as a developer's own edits would be. Neither
 * workbench node's config holds anything: both read their tables from the form folder.
 */
export async function buildWorkbenchPipeline(page: Page): Promise<void> {
  const status = await page.evaluate(
    async () => {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      const read = await fetch("/api/pipeline", { headers })
      if (!read.ok) throw new Error(`GET /api/pipeline ${read.status}`)
      const document = await read.json()
      type DocumentNode = {
        recovery_id: string
        label: string
        decorator_name: string
        node_type: string | null
        description: string
        display_position: { x: number; y: number }
        config: Record<string, unknown> | null
      }
      type DocumentEdge = {
        recovery_id: string
        source_recovery_id: string
        target_recovery_id: string
        source_handle: string | null
        target_handle: string | null
      }
      const replaced = new Set(["quotes", "priced"])
      const nodes = (document.nodes as DocumentNode[])
        .filter((node) => !replaced.has(node.label))
        .map((node) => ({
          id: node.recovery_id,
          type: node.node_type ?? node.decorator_name,
          position: node.display_position,
          data: {
            label: node.label,
            description: node.description,
            nodeType: node.node_type ?? node.decorator_name,
            ...(node.config === null ? {} : { config: node.config }),
          },
        }))
      const kept = new Set(nodes.map((node) => node.id))
      const edges = (document.edges as DocumentEdge[])
        .filter((edge) => kept.has(edge.source_recovery_id) && kept.has(edge.target_recovery_id))
        .map((edge) => ({
          id: edge.recovery_id,
          source: edge.source_recovery_id,
          target: edge.target_recovery_id,
          sourceHandle: edge.source_handle,
          targetHandle: edge.target_handle,
        }))
      nodes.push(
        {
          id: "quote",
          type: "custom",
          position: { x: 60, y: 720 },
          data: { label: "quote", description: "", nodeType: "workbenchInput", config: {} },
        },
        {
          id: "premium",
          type: "custom",
          position: { x: 380, y: 720 },
          data: {
            label: "premium",
            description: "",
            nodeType: "polars",
            config: { code: "df = policy.with_columns(premium=pl.col('limit') * 0.1)" },
          },
        },
        {
          id: "response",
          type: "custom",
          position: { x: 700, y: 720 },
          data: { label: "response", description: "", nodeType: "workbenchOutput", config: {} },
        },
      )
      edges.push(
        { id: "e_quote_premium", source: "quote", target: "premium", sourceHandle: "policy", targetHandle: null },
        { id: "e_premium_response", source: "premium", target: "response", sourceHandle: null, targetHandle: "pricing" },
      )
      const saved = await fetch("/api/pipeline/save", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: document.pipeline_name ?? "main",
          description: document.pipeline_description ?? "",
          source_file: document.source_file,
          base_revision: document.source_revision,
          preamble: document.preamble ?? "",
          preserved_blocks: document.preserved_blocks,
          sources: document.sources,
          active_source: document.active_source ?? "live",
          graph: { nodes, edges },
        }),
      })
      if (!saved.ok) throw new Error(`POST /api/pipeline/save ${saved.status}: ${await saved.text()}`)
      return (await saved.json()).status as string
    },
  )
  expect(status).toBe("saved")
}
