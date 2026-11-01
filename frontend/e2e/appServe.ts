/**
 * The underwriter app through `haute app serve` on the journeys' project (specs/workbench):
 * its page built from the frontend as it is, the app started as an analyst starts it in the
 * project, building the project's release and serving it on loopback, and the `dev`
 * environment's quote store, a SQLite file its connection in connections.toml names, its
 * tables made and changed by running the SQL Haute writes, as the people who own a database
 * run it, and read with plain SQL.
 */
import { execFileSync, spawn, spawnSync, type ChildProcess } from "node:child_process"
import { appendFileSync, existsSync, writeFileSync } from "node:fs"
import { createServer } from "node:net"
import { resolve } from "node:path"
import { expect } from "@playwright/test"

import { e2eProjectRoot, repoRoot } from "./projectIsolation"

const frontendDir = resolve(repoRoot, "frontend")
/** Where `dev`'s connection puts its database, from the project root: git ignores `.haute/`. */
const STORE_PATH = ".haute/app/store.sqlite"
/** The `dev` environment's quote store: the file `haute app serve` keeps the quotes saved in it in. */
export const storeFile = resolve(e2eProjectRoot, STORE_PATH)
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
 * Pin the journeys' project to the Python `uv run` starts the app on, `major.minor`: a
 * release is built only on the Python its project pins, and the project's scaffold pins
 * Haute's default, which the browser job's Python need not be. The project's reset puts
 * the scaffold's pin back.
 */
function pinProjectPython(): void {
  const version = execFileSync(
    "uv",
    ["run", "--project", repoRoot, "python", "-I", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
    { cwd: e2eProjectRoot, encoding: "utf8" },
  ).trim()
  writeFileSync(resolve(e2eProjectRoot, ".python-version"), `${version}\n`)
}

/**
 * `haute app serve` started in the journeys' project, as an analyst starts it, with the page
 * in `staticDir`: answered once it has built the project's release and answers its health
 * check, its quotes open or closed as `dev`'s tables fit the release's form; refused with its
 * output when it stops first or does not answer in time.
 */
export async function startAppServe(staticDir: string): Promise<AppServe> {
  pinProjectPython()
  const port = await freePort()
  const url = `http://127.0.0.1:${port}`
  // The repository's Haute, in the project: `uv run` there would make the project an
  // environment of its own from its pyproject.toml.
  const child = spawn("uv", ["run", "--project", repoRoot, "haute", "app", "serve", "--port", String(port), "--static-dir", staticDir], {
    cwd: e2eProjectRoot,
    // Its output read as it comes; a piped command writes UTF-8 whatever the locale.
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
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

/** Run `program`, Python, with the repository's Haute, `args` its arguments; what it prints. */
function python(program: string, ...args: string[]): string {
  return execFileSync("uv", ["run", "python", "-I", "-", ...args], { cwd: repoRoot, input: program, encoding: "utf8" })
}

/**
 * Declare the journeys' project's `dev` environment on SQLite, give it a connection in
 * connections.toml at `STORE_PATH`, and make its tables from the SQL Haute writes for the
 * project's form from none, as its owner would: a fresh file each time. After the workbench
 * is enabled and its form written; the project's reset takes haute.toml back.
 */
export function makeDevStore(): void {
  appendFileSync(resolve(e2eProjectRoot, "haute.toml"), '\n[workbench.store.dev]\nprovider = "sqlite"\n')
  writeFileSync(resolve(e2eProjectRoot, "connections.toml"), `[dev]\nprovider = "sqlite"\ndatabase = "${STORE_PATH}"\n`)
  python(
    [
      "import sqlite3, sys",
      "from pathlib import Path",
      "from haute._quote_store import offline_plan, render_sql",
      "from haute._workbench_config import read_workbench_config",
      "from haute._workbench_folder import read_form_document",
      "from haute._workbench_form import with_quote_details",
      "root, store = Path(sys.argv[1]), Path(sys.argv[2])",
      "form = with_quote_details(read_form_document(read_workbench_config(root)).spec)",
      "store.parent.mkdir(parents=True, exist_ok=True)",
      "store.unlink(missing_ok=True)",
      "connection = sqlite3.connect(store)",
      "try:",
      "    connection.executescript(render_sql(offline_plan(None, form), form, 'sqlite'))",
      "finally:",
      "    connection.close()",
    ].join("\n"),
    e2eProjectRoot,
    storeFile,
  )
}

/** Run `sql` on the `dev` environment's quote store, as its owner runs the SQL Haute writes. */
export function runOnStore(sql: string): void {
  // Handed over as a file beside the store, which the project's reset cleans away.
  const script = resolve(e2eProjectRoot, ".haute", "app", "change.sql")
  writeFileSync(script, sql)
  python(
    [
      "import sqlite3, sys",
      "from pathlib import Path",
      "connection = sqlite3.connect(sys.argv[1])",
      "try:",
      "    connection.executescript(Path(sys.argv[2]).read_text(encoding='utf-8'))",
      "finally:",
      "    connection.close()",
    ].join("\n"),
    storeFile,
    script,
  )
}

/** A row of the quote store as SQLite holds it, by column name. */
export type StoreRow = Record<string, string | number | null>

/** A query of the quote store: plain SQL, and the values of its `?` placeholders. */
export interface StoreQuery {
  sql: string
  params?: ReadonlyArray<string | number>
}

/**
 * What each of `queries` reads from the `dev` quote store with plain SQL, by the name it is
 * given: the rows as SQLite holds them, the file opened read only.
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
