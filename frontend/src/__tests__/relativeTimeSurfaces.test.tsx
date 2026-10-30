/**
 * One instant reads the same in every panel (specs/frontend-shared): each surface is given
 * the instant in its own form, the git history's and the breadcrumb's ISO strings, the
 * assistant's, the Files panel's, Explore's and an import's Unix seconds, and says it as
 * the one rule (`relativeTime`) does.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, waitFor } from "@testing-library/react"

const api = vi.hoisted(() => ({
  getWorkingBranch: vi.fn(),
  getMilestones: vi.fn(),
  listProjectFiles: vi.fn(),
}))

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  getWorkingBranch: api.getWorkingBranch,
  getMilestones: api.getMilestones,
  getPendingSaves: vi.fn(() => Promise.resolve({ saves: [] })),
  getMilestoneSaves: vi.fn(() => Promise.resolve({ saves: [] })),
  getGitGraph: vi.fn(() => Promise.resolve({ working_branch: null, order: [], branches: [] })),
  getWorkingBranches: vi.fn(() => Promise.resolve({ current: "dev", deleted: [], branches: [] })),
  getGitPrefs: vi.fn(() => Promise.resolve({ skip_switch_confirm: false })),
  getGitRemotes: vi.fn(() => Promise.resolve({ remotes: [], working_branch: null })),
}))

vi.mock("../api/projectFiles", () => ({
  listProjectFiles: api.listProjectFiles,
  uploadProjectFile: vi.fn(),
  deleteProjectFile: vi.fn(),
  projectFileDownloadUrl: () => "",
}))

import CommitBreadcrumb from "../components/CommitBreadcrumb"
import FilesPanel from "../panels/FilesPanel"
import GitPanel from "../panels/GitPanel"
import SessionList from "../panels/assistant/SessionList"
import { DatasetSnapshotCard } from "../panels/explore/ExploreSummaryCards"
import { clearGitPanelCaches } from "../panels/gitPanelCache"
import useAssistantStore from "../stores/useAssistantStore"
import { resetGitStoreForTests } from "../stores/useGitStore"
import { importedTitle } from "../utils/importedTitle"
import { relativeTime } from "../utils/relativeTime"
import { makeInputCacheSnapshot } from "../test-utils/factories"

// jsdom has no ResizeObserver; the git panel's rail overlay measures with one.
class NoResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const NOW = new Date("2026-10-29T12:00:00Z")

describe.each([
  ["a recent instant", new Date("2026-10-29T08:40:00Z")],
  ["an instant past a week", new Date("2026-10-12T09:30:00Z")],
])("%s", (_name, instant) => {
  const iso = instant.toISOString()
  const seconds = instant.getTime() / 1000
  const said = relativeTime(instant, NOW)

  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(NOW)
    globalThis.ResizeObserver = NoResizeObserver as unknown as typeof ResizeObserver
    clearGitPanelCaches()
    await resetGitStoreForTests()
    api.getWorkingBranch.mockResolvedValue({
      working_branch: "dev",
      current_branch: "dev-save",
      state: "ready",
      eligible_branches: [],
      identity_set: true,
      user_name: "A",
      user_email: "a@example.com",
      last_save_sha: null,
      errors: [],
    })
    api.getMilestones.mockResolvedValue({
      working_branch: "dev",
      entries: [{ sha: "m1", short_sha: "m1abc", message: "First milestone", timestamp: iso, version_label: null }],
    })
    api.listProjectFiles.mockResolvedValue({
      place: "pipeline",
      path: "",
      root: "rating/files",
      writable: true,
      entries: [
        { name: "lookup.csv", path: "lookup.csv", kind: "file", size: 3, modified: seconds, revision: "3-lookup", status: null },
      ],
    })
    useAssistantStore.setState({
      sessions: [{ sessionId: "s1", title: "Claims", createdAt: seconds, lastUsed: seconds, messageCount: 2 }],
      sessionsStatus: "ready",
    })
  })

  afterEach(async () => {
    cleanup()
    vi.useRealTimers()
    await resetGitStoreForTests()
  })

  it("reads the same in every panel", async () => {
    render(
      <>
        <GitPanel onClose={vi.fn()} />
        <CommitBreadcrumb
          context={{
            sha: "s9",
            short_sha: "s9abc",
            message: "Add factor",
            timestamp: iso,
            is_root: false,
            is_milestone: false,
            version_label: null,
            nearest_milestone: { sha: "m1", short_sha: "m1abc", message: "First milestone", version_label: null, is_root: false },
            distance: 1,
            delta_from_base: null,
          }}
        />
        <SessionList currentSourceFile="main.py" />
        <FilesPanel place="pipeline" onClose={vi.fn()} />
        <DatasetSnapshotCard
          report={{
            row_count: 1,
            column_count: 0,
            columns: [],
            overview_summary: {
              data_quality: { issue_count: 0, issues: [], duplicate_row_count: 0, duplicate_ratio: 0 },
              categorical_summary: [],
            },
            data_version: "v1",
            generated_at: seconds,
            source: "pricing",
            producer_node_id: "prep",
          }}
        />
      </>,
    )

    expect(said).not.toBe("")
    await waitFor(() => expect(screen.getByTestId("git-panel-milestone")).toHaveTextContent(`m1abc · ${said}`))
    expect(screen.getByTestId("commit-breadcrumb")).toHaveTextContent(`s9abc·${said}`)
    expect(screen.getByTestId("assistant-session-item")).toHaveTextContent(`${said} · 2 messages`)
    expect((await screen.findByText("lookup.csv")).closest("li")).toHaveTextContent(said)
    expect(screen.getByTestId("explore-dataset-snapshot-card")).toHaveTextContent(`Cached${said}`)
    expect(
      importedTitle(
        makeInputCacheSnapshot({
          schema_version: 1,
          identity_digest: "digest",
          state: "ready",
          freshness: "unknown",
          generation: {
            generation_id: "g1",
            row_count: 1,
            column_count: 1,
            columns: {},
            size_bytes: 1,
            created_at: seconds,
            build_class: "bounded",
          },
        }),
        NOW,
      ),
    ).toBe(`Imported ${said}`)
  })
})
