import { execFileSync } from "node:child_process"

import { expect, test, type Page } from "@playwright/test"

import {
  e2eProjectRoot,
  e2eWorkingBranch,
  repoRoot,
  resetE2eProject,
} from "./projectIsolation"

// Topology assertions for the graph rail (branch-only: the rail and its
// data-* vocabulary don't exist on a main-built install — never copy this
// file into the main-parity clone).
//
// The rich fixture is the engine-built composite from
// scripts/e2e_git_topologies.py, seeded ONCE for its describe block (A-1):
// resetE2eProject() (which also scrubs stale version/* tags) → the seeding
// CLI. The seed is one-shot per reset (its branch names and version/<label>
// tags are fixed), and every test here is repo-read-only (peek/expand/select
// are client state over GET endpoints), so a beforeAll seed plus a fresh page
// per test gives the same isolation as the regression suite's per-test
// reset without re-paying the ~10s engine build each time.
//
// Fixture roles (the branches/commits keys of the CLI's JSON summary):
//   work     [M7 M6 M5 M4 M3 M2 M1 R] + pending P1 P2   (current, viewed)
//   crystal  [X M5 .. R]   crystallized at pending save S1, attaches at M5
//   fork-old [FO3 FO2 FO1 M2 M1 R]; fork-of-fork at FO1; twin-a/-b at M4;
//   indie-a/-b at R; old-idea archived (excluded from the rail silently).
//
// With the default lane cap of 5, viewing `work` draws crystal / fork-old /
// twin-a / twin-b (`order` priority) as departures on lanes 1-4 and counts
// fork-of-fork (fork point off the visible spine) plus indie-a / indie-b
// (over the lane budget) as "+3 elsewhere".

interface SeededTopology {
  case: string
  working: string
  branches: Record<string, string>
  commits: Record<string, string>
}

function seedRichTopology(): SeededTopology {
  const stdout = execFileSync(
    "uv",
    ["run", "python", "scripts/e2e_git_topologies.py", "--seed", e2eProjectRoot, "--case", "rich"],
    { cwd: repoRoot, encoding: "utf8" },
  )
  // Engine log lines may precede the JSON summary on stdout; the summary's
  // root brace is the only line that is exactly "{".
  const lines = stdout.split(/\r?\n/)
  const start = lines.indexOf("{")
  if (start === -1) {
    throw new Error(`e2e_git_topologies printed no JSON summary:\n${stdout}`)
  }
  return JSON.parse(lines.slice(start).join("\n")) as SeededTopology
}

async function openGitPanel(page: Page): Promise<void> {
  await page.getByTestId("branch-indicator-name").click()
  await expect(page.getByTestId("git-panel")).toBeVisible()
}

const dotSelector = (sha: string): string => `[data-testid="git-graph-dot"][data-sha="${sha}"]`

const magnifierSelector = (sha: string): string =>
  `[data-testid="git-graph-magnifier"][data-expands="${sha}"]`

const chipSelector = (branch: string): string =>
  `[data-testid="git-graph-branch-chip"][data-branch="${branch}"]`

// The curve at the fork row is the only fork edge carrying from/to lanes —
// a departure lane's vertical passes above it are fork edges without them.
const forkCurveSelector = (branch: string): string =>
  `[data-testid="git-graph-edge"][data-edge-kind="fork"][data-branch="${branch}"][data-from-lane]`

const transitionSelector = '[data-testid="git-graph-edge"][data-edge-kind="transition"]'

test.describe("git graph rail — default seed", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("@smoke rail renders a single lane holding the root milestone dot", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    await expect(page.getByTestId("git-graph-rail").first()).toBeVisible()

    // One pair, no forks: exactly the root milestone's dot on lane 0, no
    // branch chips, no overflow, and a one-lane rail (1 × 12px + 8px gutter).
    const dots = page.locator('[data-testid="git-graph-dot"]')
    await expect(dots).toHaveCount(1)
    await expect(dots).toHaveAttribute("data-kind", "milestone")
    await expect(dots).toHaveAttribute("data-lane", "0")
    await expect(dots).toHaveAttribute("data-branch", e2eWorkingBranch)
    await expect(page.getByTestId("git-graph-rail").first()).toHaveCSS("width", "20px")
    await expect(page.getByTestId("git-graph-branch-chip")).toHaveCount(0)
    await expect(page.getByTestId("git-graph-overflow")).toHaveCount(0)
  })
})

test.describe("git graph rail — rich topology", () => {
  let topo: SeededTopology

  test.beforeAll(() => {
    // The engine build is subprocess-bound (~10s healthy, slower under the
    // gate's parallel load) — give the one-off hook its own headroom.
    test.setTimeout(120_000)
    resetE2eProject()
    topo = seedRichTopology()
  })

  test.beforeEach(async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)
  })

  test("viewed spine draws on lane 0 with departure chips and an overflow count", async ({
    page,
  }) => {
    // One rail cell per visual row: 2 pending saves + 8 milestones (M7..M1, R).
    await expect(page.getByTestId("git-graph-rail")).toHaveCount(10)

    // Lane budget: the viewed lane + 4 drawn departures = 5 → min(5,5)*12+8.
    await expect(page.getByTestId("git-graph-rail").first()).toHaveCSS("width", "68px")

    // Every spine milestone is owned by `work` (the fork-tree root): lane 0,
    // colour index 0 (its position in `order`).
    await expect(page.locator('[data-testid="git-graph-dot"][data-kind="milestone"]')).toHaveCount(
      8,
    )
    for (const key of ["M7", "M6", "M5", "M4", "M3", "M2", "M1", "R"]) {
      const dot = page.locator(dotSelector(topo.commits[key]))
      await expect(dot).toHaveAttribute("data-lane", "0")
      await expect(dot).toHaveAttribute("data-color-index", "0")
      await expect(dot).toHaveAttribute("data-branch", topo.branches.work)
    }

    // Top chips in lane order (labels are the last path segment); the rest
    // fold into the overflow counter; the archived pair is excluded silently.
    await expect(page.getByTestId("git-graph-branch-chip")).toHaveText([
      "crystal",
      "fork-old",
      "twin-a",
      "twin-b",
    ])
    await expect(page.getByTestId("git-graph-overflow")).toHaveText("+3 elsewhere")
    await expect(page.locator(chipSelector(topo.branches.archived))).toHaveCount(0)
  })

  test("departure curves leave the spine at each child's fork-point row", async ({ page }) => {
    const departures = [
      { child: topo.branches.crystal, forkPoint: topo.commits.M5, toLane: "1" },
      { child: topo.branches.fork_old, forkPoint: topo.commits.M2, toLane: "2" },
      { child: topo.branches.twin_a, forkPoint: topo.commits.M4, toLane: "3" },
      { child: topo.branches.twin_b, forkPoint: topo.commits.M4, toLane: "4" },
    ]
    for (const { child, forkPoint, toLane } of departures) {
      const curve = page.locator(forkCurveSelector(child))
      await expect(curve).toHaveCount(1)
      await expect(curve).toHaveAttribute("data-from-lane", "0")
      await expect(curve).toHaveAttribute("data-to-lane", toLane)
      // The curve lives in the same rail cell as its fork-point milestone dot.
      const forkCell = page.locator('[data-testid="git-graph-rail"]', {
        has: page.locator(dotSelector(forkPoint)),
      })
      await expect(forkCell.locator(forkCurveSelector(child))).toHaveCount(1)
    }
  })

  test("magnifiers sit exactly on collapsed edges that hide folded saves", async ({ page }) => {
    // M7..M1 all fold ≥1 save and are collapsed → 7 magnifiers. The root
    // folds nothing and is the window-final row — never a magnifier.
    const magnifiers = page.getByTestId("git-graph-magnifier")
    await expect(magnifiers).toHaveCount(7)
    await expect(page.locator(magnifierSelector(topo.commits.R))).toHaveCount(0)

    // Expanding through the magnifier inserts the folded save row and
    // retires the affordance (row adjacency below M7 is broken).
    await page.locator(magnifierSelector(topo.commits.M7)).click()
    const saves = page.getByTestId("git-panel-save")
    await expect(saves).toHaveCount(1)
    await expect(saves).toContainText("Save s3")
    await expect(page.locator(magnifierSelector(topo.commits.M7))).toHaveCount(0)
    await expect(magnifiers).toHaveCount(6)

    // Row-click collapse restores the edge — and its magnifier.
    await page.getByTestId("git-panel-milestone").filter({ hasText: "Milestone 7" }).click()
    await expect(saves).toHaveCount(0)
    await expect(page.locator(magnifierSelector(topo.commits.M7))).toBeVisible()
    await expect(magnifiers).toHaveCount(7)
  })

  test("pending saves render hollow dots on the viewed lane", async ({ page }) => {
    const pendingDots = page
      .getByTestId("git-panel-pending")
      .locator('[data-testid="git-graph-dot"][data-kind="pending"]')
    await expect(pendingDots).toHaveCount(2)
    for (const key of ["P1", "P2"]) {
      const dot = page.locator(dotSelector(topo.commits[key]))
      await expect(dot).toHaveAttribute("data-kind", "pending")
      await expect(dot).toHaveAttribute("data-lane", "0")
      await expect(dot).toHaveAttribute("data-branch", topo.branches.work)
    }
    // Pending saves are always visible — nothing collapsible on their edges.
    await expect(
      page.getByTestId("git-panel-pending").getByTestId("git-graph-magnifier"),
    ).toHaveCount(0)
  })

  test("peeking a fork via its chip moves emphasis and draws the lane transition", async ({
    page,
  }) => {
    await page.locator(chipSelector(topo.branches.fork_old)).click()

    const banner = page.getByTestId("git-panel-peeking")
    await expect(banner).toBeVisible()
    await expect(banner).toContainText("fork-old")

    // The peeked branch takes lane 0 in its own colour (order index 2)…
    for (const key of ["FO3", "FO2", "FO1"]) {
      const dot = page.locator(dotSelector(topo.commits[key]))
      await expect(dot).toHaveAttribute("data-lane", "0")
      await expect(dot).toHaveAttribute("data-color-index", "2")
      await expect(dot).toHaveAttribute("data-branch", topo.branches.fork_old)
    }
    // …and from the fork point down the spine belongs to `work`, one lane over.
    for (const key of ["M2", "M1", "R"]) {
      const dot = page.locator(dotSelector(topo.commits[key]))
      await expect(dot).toHaveAttribute("data-lane", "1")
      await expect(dot).toHaveAttribute("data-color-index", "0")
      await expect(dot).toHaveAttribute("data-branch", topo.branches.work)
    }

    // Exactly one transition, at the fork point's own row, child → parent
    // lane; its data-branch names the landing owner.
    const transition = page.locator(transitionSelector)
    await expect(transition).toHaveCount(1)
    await expect(transition).toHaveAttribute("data-from-lane", "0")
    await expect(transition).toHaveAttribute("data-to-lane", "1")
    await expect(transition).toHaveAttribute("data-branch", topo.branches.work)
    const forkCell = page.locator('[data-testid="git-graph-rail"]', {
      has: page.locator(dotSelector(topo.commits.M2)),
    })
    await expect(forkCell.locator(transitionSelector)).toHaveCount(1)

    // The rail re-derives around the new viewpoint: fork-old's own children
    // become the drawn departures, everything else counts as elsewhere.
    await expect(page.getByTestId("git-graph-branch-chip")).toHaveText([
      "fork-of-fork",
      "indie-a",
      "indie-b",
    ])
    await expect(page.getByTestId("git-graph-overflow")).toHaveText("+3 elsewhere")
  })

  test("a crystallized fork's transition edge carries the magnifier", async ({ page }) => {
    await page.locator(chipSelector(topo.branches.crystal)).click()

    const banner = page.getByTestId("git-panel-peeking")
    await expect(banner).toBeVisible()
    await expect(banner).toContainText("crystal")

    // The anchoring milestone X attaches at the spawning TIP milestone (M5),
    // not at the save the user forked from (forked_from ≠ fork_point_sha).
    const anchor = page.locator(dotSelector(topo.commits.X))
    await expect(anchor).toHaveAttribute("data-lane", "0")
    await expect(anchor).toHaveAttribute("data-color-index", "1")
    await expect(anchor).toHaveAttribute("data-branch", topo.branches.crystal)

    const transition = page.locator(transitionSelector)
    await expect(transition).toHaveCount(1)
    await expect(transition).toHaveAttribute("data-from-lane", "0")
    await expect(transition).toHaveAttribute("data-to-lane", "1")

    // X is collapsed and folds the spawning branch's pre-fork pending save,
    // so the transition edge below it must carry the magnifier (A-4) — and
    // expanding through it surfaces exactly that save.
    await expect(page.locator(magnifierSelector(topo.commits.X))).toBeVisible()
    await page.locator(magnifierSelector(topo.commits.X)).click()
    const saves = page.getByTestId("git-panel-save")
    await expect(saves).toHaveCount(1)
    await expect(saves).toContainText("Save s1")
    await expect(page.locator(magnifierSelector(topo.commits.X))).toHaveCount(0)
  })

  test("clicking a viewed-lane dot selects its row without toggling expansion", async ({
    page,
  }) => {
    const dot = page.locator(dotSelector(topo.commits.M5))
    await dot.click()

    await expect(dot).toHaveAttribute("data-selected", "true")
    await expect(
      page.getByTestId("git-panel-milestone").filter({ hasText: "Milestone 5" }),
    ).toHaveAttribute("data-selected", "true")
    // stopPropagation discipline: the selection click must not double as the
    // row's expand click (neither the loading placeholder nor save rows).
    await expect(page.getByText("Loading saves…")).toHaveCount(0)
    await expect(page.getByTestId("git-panel-save")).toHaveCount(0)
  })
})
