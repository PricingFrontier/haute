/**
 * The underwriter app through `haute app serve` on the journeys' project (specs/workbench):
 * its page built from the frontend as it is, the app started as an analyst starts it in the
 * project, building the project's release and serving it on loopback, and dev's quote store,
 * the SQLite file it keeps its quotes in, read with plain SQL.
 */
import { execFileSync, spawn, spawnSync, type ChildProcess } from "node:child_process"
import { existsSync } from "node:fs"
import { createServer } from "node:net"
import { resolve } from "node:path"
import { expect } from "@playwright/test"

import { e2eProjectRoot, repoRoot } from "./projectIsolation"

const frontendDir = resolve(repoRoot, "frontend")
/** Dev's quote store: the file `haute app serve` keeps the quotes saved in it in. */
export const storeFile = resolve(e2eProjectRoot, ".haute", "app", "store.sqlite")
/** How long the app may take to build the project's release and answer its health check. */
const START_TIMEOUT_MS = 180_000
/** The most of a process's output a failure shows: its last characters. */
const OUTPUT_SHOWN = 20_000

/** A process's output as it runs: both its streams as they came, and any failure to start it. */
function collect(child: ChildProcess): () => string {
  const chunks: string[] = []
  for (const stream of [child.stdout, child.stderr]) {
    stream?.setEncoding("utf8")
    stream?.on("data", (chunk: string) => chunks.push(chunk))
  }
  child.once("error", (error) => chunks.push(`${String(error)}\n`))
  return () => chunks.join("").slice(-OUTPUT_SHOWN)
}

/** The end of `child`: once it has exited, or could not be started. */
function ending(child: ChildProcess): { ended: Promise<void>; over: () => boolean } {
  let over = false
  const ended = new Promise<void>((resolveEnded) => {
    const end = () => {
      over = true
      resolveEnded()
    }
    child.once("exit", end)
    child.once("error", end)
  })
  return { ended, over: () => over }
}

/**
 * Build the underwriter app's page into `outDir` as `npm run build` builds it into Haute's
 * static folder. The browser job builds no frontend, and without its page the app answers
 * every page with a 503, so the journey builds the page as the source stands.
 */
export async function buildAppPage(outDir: string): Promise<void> {
  const child = spawn(
    process.execPath,
    [resolve(frontendDir, "node_modules", "vite", "bin", "vite.js"), "build", "--config", "vite.app.config.ts", "--outDir", outDir, "--emptyOutDir", "--logLevel", "warn"],
    { cwd: frontendDir, stdio: ["ignore", "pipe", "pipe"] },
  )
  const output = collect(child)
  await ending(child).ended
  if (child.exitCode !== 0 || !existsSync(resolve(outDir, "app.html"))) {
    throw new Error(`The app's page did not build into ${outDir} (exit ${child.exitCode}):\n${output()}`)
  }
}

/** A port nothing on loopback listens on now. */
async function freePort(): Promise<number> {
  const server = createServer()
  await new Promise<void>((listening, failed) => {
    server.once("error", failed)
    server.listen(0, "127.0.0.1", listening)
  })
  const address = server.address()
  await new Promise<void>((closed, failed) => server.close((error) => (error ? failed(error) : closed())))
  if (address === null || typeof address === "string") throw new Error(`no port was given: ${String(address)}`)
  return address.port
}

/** Whether anything answers `url`'s health check. */
async function answers(url: string): Promise<boolean> {
  try {
    return (await fetch(`${url}/health`)).ok
  } catch {
    // Refused: nothing listens there.
    return false
  }
}

export interface AppServe {
  /** Where the app is: `http://127.0.0.1:<port>`. */
  url: string
  /** The release it serves, as its health check names it. */
  release: string
  /** What the command has printed so far. */
  output: () => string
  /** Stop it and every process it started; once stopped, nothing of it holds the project's files. */
  stop: () => Promise<void>
}

/**
 * `haute app serve` started in the journeys' project, as an analyst starts it, with the page
 * in `staticDir`: answered once it has built the project's release, made dev's quote store
 * hold the form's tables and answers its health check; refused with its output when it stops
 * first or does not answer in time.
 */
export async function startAppServe(staticDir: string): Promise<AppServe> {
  const port = await freePort()
  const url = `http://127.0.0.1:${port}`
  // The repository's Haute, in the project: `uv run` there would make the project an
  // environment of its own from its pyproject.toml.
  const child = spawn("uv", ["run", "--project", repoRoot, "haute", "app", "serve", "--port", String(port), "--static-dir", staticDir], {
    cwd: e2eProjectRoot,
    // Its output is read as UTF-8, as it comes: a piped stdout on Windows is otherwise
    // written in the ANSI code page, which cannot encode the marks the command prints.
    env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    // A group of its own on POSIX, so stopping it stops the Python process uv starts too.
    detached: process.platform !== "win32",
  })
  const output = collect(child)
  const { over } = ending(child)

  const stop = async () => {
    if (!over()) {
      if (process.platform === "win32") {
        // On Windows uv's Python child outlives the process it was started from: the whole tree goes.
        spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" })
      } else if (child.pid !== undefined) {
        try {
          process.kill(-child.pid, "SIGKILL")
        } catch (error) {
          // The group ended on its own meanwhile.
          if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error
        }
      }
    }
    await expect.poll(over, { message: "haute app serve has exited" }).toBe(true)
    await expect.poll(() => answers(url), { message: `nothing answers at ${url} once haute app serve has stopped` }).toBe(false)
  }

  const state = async () => {
    if (over()) return "stopped"
    return (await answers(url)) ? "answering" : "starting"
  }
  try {
    await expect.poll(state, { message: `haute app serve answers ${url}/health`, timeout: START_TIMEOUT_MS }).not.toBe("starting")
  } catch (error) {
    await stop()
    throw new Error(`haute app serve did not answer its health check:\n${output()}`, { cause: error })
  }
  if (over()) {
    throw new Error(`haute app serve stopped (exit ${child.exitCode ?? child.signalCode}) before it answered:\n${output()}`)
  }
  const health = (await (await fetch(`${url}/health`)).json()) as { release: string }
  return { url, release: health.release, output, stop }
}

/** A row of the quote store as SQLite holds it, by column name. */
export type StoreRow = Record<string, string | number | null>

/** A query of the quote store: plain SQL, and the values of its `?` placeholders. */
export interface StoreQuery {
  sql: string
  params?: ReadonlyArray<string | number>
}

/**
 * What each of `queries` reads from dev's quote store with plain SQL, by the name it is given:
 * the rows as SQLite holds them, the file opened read only.
 */
export function readStore<Name extends string>(queries: Record<Name, StoreQuery>): Record<Name, StoreRow[]> {
  const program = [
    "import json, sqlite3",
    "from pathlib import Path",
    `path, queries = json.loads(${JSON.stringify(JSON.stringify([storeFile, queries]))})`,
    "connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)",
    "connection.row_factory = sqlite3.Row",
    "try:",
    "    read = {",
    "        name: [dict(row) for row in connection.execute(query['sql'], query.get('params', []))]",
    "        for name, query in queries.items()",
    "    }",
    "finally:",
    "    connection.close()",
    "print(json.dumps(read))",
  ].join("\n")
  const stdout = execFileSync("uv", ["run", "python", "-I", "-"], { cwd: repoRoot, input: program, encoding: "utf8" })
  return JSON.parse(stdout) as Record<Name, StoreRow[]>
}
