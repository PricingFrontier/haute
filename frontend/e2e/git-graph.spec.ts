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
// tags are fixed), and every test in the rich block is repo-read-only
// (peek/expand and opening context menus are client state over GET
// endpoints), so a beforeAll seed plus a fresh page per test gives the same
// isolation as the regression suite's per-test reset without re-paying the
// ~10s engine build each time. Mutating flows (the in-app switch + undo)
// live in the default-seed block, which resets per test.
//
// Fixture roles (the branches/commits keys of the CLI's JSON summary):
//   work     [M7 M6 M5 M4 M3 M2 M1 R] + pending P1 P2   (current, viewed)
//   crystal  [X M5 .. R]   crystallized at save S1 (folded into M6): the
//            fork point is M5, the SOURCE is S1, the CREDIT milestone M6
//   fork-old [FO3 FO2 FO1 M2 M1 R]; fork-of-fork at FO1; twin-a/-b at M4;
//   indie-a/-b at R; old-idea archived (muted stub in the parent's colour).
//
// Rail v2 vocabulary: departures draw spawn STUBS (curve + dotted tail) in
// slot columns right of the lanes instead of full-height lanes; ancestors
// keep full lanes and their rails run to the top of the list; expanded saves
// sit on a dotted sub-rail beside the spine.

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

const spawnSelector = (branch: string): string =>
  `[data-testid="git-graph-spawn"][data-branch="${branch}"]`

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
    // branch chips, no overflow, and a one-lane rail (14px magnifier gutter + 1 × 12px + 8px gutter).
    const dots = page.locator('[data-testid="git-graph-dot"]')
    await expect(dots).toHaveCount(1)
    await expect(dots).toHaveAttribute("data-kind", "milestone")
    await expect(dots).toHaveAttribute("data-lane", "0")
    await expect(dots).toHaveAttribute("data-branch", e2eWorkingBranch)
    await expect(page.getByTestId("git-graph-rail").first()).toHaveCSS("width", "34px")
    await expect(page.getByTestId("git-graph-branch-chip")).toHaveCount(0)
    await expect(page.getByTestId("git-graph-overflow")).toHaveCount(0)
  })

  test("switching via the lane menu is in-app and toolbar Undo reverses it", async ({ page }) => {
    await page.goto("/")
    await openGitPanel(page)

    // Spin off a parallel branch, then switch to it from the rail's lane
    // context menu — no page reload: the panel stays open throughout.
    await page.getByTestId("branch-manager-create-input").fill("lane-switch")
    await page.getByTestId("branch-manager-create").click()
    const chip = page.locator('[data-testid="git-graph-branch-chip"]')
    await expect(chip).toHaveCount(1, { timeout: 10_000 })

    await page
      .locator('[data-testid="git-graph-edge"][data-edge-kind="spine"]')
      .first()
      .dispatchEvent("contextmenu")
    await expect(page.getByTestId("git-graph-lane-menu")).toBeVisible()
    // Lane 0 is the current branch — switching to it is disabled; viewing is
    // pointless too. Close and drive the switch from the branch manager row
    // menu instead (same in-app path).
    await expect(page.getByTestId("git-graph-lane-menu-switch")).toBeDisabled()
    await page.locator(".fixed.inset-0").first().click()
    await expect(page.getByTestId("git-graph-lane-menu")).toHaveCount(0)

    const row = page.getByTestId("branch-manager-branch").filter({ hasText: "lane-switch" })
    await row.dispatchEvent("contextmenu")
    await expect(page.getByTestId("branch-manager-row-menu")).toBeVisible()
    await page.getByTestId("branch-manager-row-menu-switch").click()
    // First switch on a fresh environment prompts a confirm.
    await expect(page.getByTestId("branch-manager-confirm-switch")).toBeVisible()
    await page.getByTestId("branch-manager-confirm-switch-go").click()

    await expect(page.getByTestId("branch-indicator-name")).toContainText("lane-switch", {
      timeout: 15_000,
    })
    // In-app: the panel never unmounted.
    await expect(page.getByTestId("git-panel")).toBeVisible()

    // The switch is an undoable history entry — toolbar Undo switches back.
    await page.getByRole("button", { name: "Undo" }).click()
    await expect(page.getByTestId("branch-indicator-name")).toContainText(e2eWorkingBranch, {
      timeout: 15_000,
    })
    await expect(page.getByTestId("git-panel")).toBeVisible()
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

  test("viewed spine draws on lane 0 with every departure chipped", async ({ page }) => {
    // One rail cell per visual row: 2 pending saves + 8 milestones (M7..M1, R).
    await expect(page.getByTestId("git-graph-rail")).toHaveCount(10)

    // Rail width: 1 lane (the viewed spine — departures reserve slot columns,
    // not lanes) + the widest anchor group (twin-a + twin-b at M4, and the
    // two indies at R, both 2 wide): 14 (magnifier gutter) + 1×12 + 2×13 (flare) + 8.
    await expect(page.getByTestId("git-graph-rail").first()).toHaveCSS("width", "60px")

    // Every spine milestone is owned by `work` (the fork-tree root): lane 0,
    // colour index 0 (its position among the non-archived order entries).
    await expect(page.locator('[data-testid="git-graph-dot"][data-kind="milestone"]')).toHaveCount(
      8,
    )
    for (const key of ["M7", "M6", "M5", "M4", "M3", "M2", "M1", "R"]) {
      const dot = page.locator(dotSelector(topo.commits[key]))
      await expect(dot).toHaveAttribute("data-lane", "0")
      await expect(dot).toHaveAttribute("data-color-index", "0")
      await expect(dot).toHaveAttribute("data-branch", topo.branches.work)
    }

    // EVERY resolvable departure chips (labels are the last path segment),
    // archived included (muted, parent-coloured). Only fork-of-fork — whose
    // fork point sits on fork-old's spine, off this window — overflows.
    await expect(page.getByTestId("git-graph-branch-chip")).toHaveText([
      "crystal",
      "fork-old",
      "twin-a",
      "twin-b",
      "old-idea",
      "indie-a",
      "indie-b",
    ])
    await expect(page.locator(chipSelector(topo.branches.archived))).toHaveAttribute(
      "data-archived",
      "true",
    )
    await expect(page.getByTestId("git-graph-overflow")).toHaveText("+1 elsewhere")
  })

  test("spawn stubs anchor at each departure's credited row with stable slots", async ({
    page,
  }) => {
    // Live departures: one stub each, slot-indexed within their anchor group.
    // crystal was spawned from save S1 (folded into M6): while M6 is
    // collapsed the CREDIT milestone takes the spawn, not the fork point M5.
    const stubs = [
      { branch: topo.branches.crystal, anchor: topo.commits.M6, slot: "0" },
      { branch: topo.branches.twin_a, anchor: topo.commits.M4, slot: "0" },
      { branch: topo.branches.twin_b, anchor: topo.commits.M4, slot: "1" },
      { branch: topo.branches.fork_old, anchor: topo.commits.M2, slot: "0" },
      { branch: topo.branches.indie_a, anchor: topo.commits.R, slot: "0" },
      { branch: topo.branches.indie_b, anchor: topo.commits.R, slot: "1" },
    ]
    for (const { branch, anchor, slot } of stubs) {
      const stub = page.locator(spawnSelector(branch))
      await expect(stub).toHaveCount(1)
      await expect(stub).toHaveAttribute("data-slot", slot)
      const anchorCell = page.locator('[data-testid="git-graph-rail"]', {
        has: page.locator(dotSelector(anchor)),
      })
      await expect(anchorCell.locator(spawnSelector(branch))).toHaveCount(1)
    }

    // The archived pair renders ONE muted stub at its spawn milestone (M1),
    // named for the PARENT branch (whose colour it borrows), counting its
    // members.
    const archivedStub = page.locator(
      '[data-testid="git-graph-spawn"][data-archived="true"]',
    )
    await expect(archivedStub).toHaveCount(1)
    await expect(archivedStub).toHaveAttribute("data-branch", topo.branches.work)
    await expect(archivedStub).toHaveAttribute("data-count", "1")
    const m1Cell = page.locator('[data-testid="git-graph-rail"]', {
      has: page.locator(dotSelector(topo.commits.M1)),
    })
    await expect(m1Cell.locator('[data-testid="git-graph-spawn"][data-archived="true"]')).toHaveCount(1)
  })

  test("expanding a milestone moves a save-spawned branch's credit onto its save row", async ({
    page,
  }) => {
    // Collapsed: crystal chips against M6 (the milestone that folded S1).
    const m6Row = page.getByTestId("git-panel-milestone").filter({ hasText: "Milestone 6" })
    await expect(m6Row.getByTestId("git-panel-fork-link")).toHaveText("crystal")

    // Expand M6: the chip and the stub move to the actual source save row,
    // and the saves sit on the dotted sub-rail beside the spine.
    await page.locator(magnifierSelector(topo.commits.M6)).click()
    const saves = page.getByTestId("git-panel-save")
    await expect(saves).toHaveCount(2)
    const s1Row = saves.filter({ hasText: "Save s1" })
    await expect(s1Row.getByTestId("git-panel-fork-link")).toHaveText("crystal")
    await expect(m6Row.getByTestId("git-panel-fork-link")).toHaveCount(0)

    const s1Cell = page.locator('[data-testid="git-graph-rail"]', {
      has: page.locator(`[data-testid="git-graph-dot"][data-sha="${topo.commits.S1}"]`),
    })
    await expect(s1Cell.locator(spawnSelector(topo.branches.crystal))).toHaveCount(1)

    // Sub-rail vocabulary: save dots + dotted sub-rail edges, and the fold
    // curves bounding the range (fold-in on M6's row, fold-out on M5's).
    await expect(
      page.locator('[data-testid="git-graph-dot"][data-kind="save"]'),
    ).toHaveCount(2)
    expect(
      await page.locator('[data-testid="git-graph-edge"][data-edge-kind="sub-rail"]').count(),
    ).toBeGreaterThanOrEqual(4)

    // Slot reservation is per anchor group — the rail width never jumps when
    // a group's spawns spread from the milestone onto its save rows.
    await expect(page.getByTestId("git-graph-rail").first()).toHaveCSS("width", "60px")
  })

  test("magnifiers toggle: zoom-in on collapsed folds, zoom-out while open", async ({ page }) => {
    // M7..M1 all fold ≥1 save and are collapsed → 7 magnifiers. The root
    // folds nothing and is the window-final row — never a magnifier.
    const magnifiers = page.getByTestId("git-graph-magnifier")
    await expect(magnifiers).toHaveCount(7)
    await expect(page.locator(magnifierSelector(topo.commits.R))).toHaveCount(0)

    // Expanding through the magnifier inserts the folded save row; the
    // button STAYS, flipped to its zoom-out form, and toggles back closed.
    const m7 = page.locator(magnifierSelector(topo.commits.M7))
    await expect(m7).not.toHaveAttribute("data-expanded", "true")
    await m7.click()
    const saves = page.getByTestId("git-panel-save")
    await expect(saves).toHaveCount(1)
    await expect(saves).toContainText("Save s3")
    await expect(m7).toHaveAttribute("data-expanded", "true")
    await expect(magnifiers).toHaveCount(7)

    await m7.click()
    await expect(saves).toHaveCount(0)
    await expect(m7).not.toHaveAttribute("data-expanded", "true")
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

  test("peeking a fork draws the ancestor's full-height rail and the lane transition", async ({
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

    // The ANCESTOR'S rail continues to the very top (its history runs on
    // alongside): the first rail cell — fork-old's tip row, above the fork
    // point — still carries a work lane line.
    const topCell = page.locator('[data-testid="git-graph-rail"]', {
      has: page.locator(dotSelector(topo.commits.FO3)),
    })
    await expect(
      topCell.locator(
        `[data-edge-kind="spine"][data-branch="${topo.branches.work}"]`,
      ),
    ).toHaveCount(1)

    // The rail re-derives around the new viewpoint: fork-old's own children
    // and the root-spawned branches chip; work's other forks (crystal, the
    // twins — fork points off this window) count as elsewhere.
    await expect(page.getByTestId("git-graph-branch-chip")).toHaveText([
      "fork-of-fork",
      "old-idea",
      "indie-a",
      "indie-b",
    ])
    await expect(page.getByTestId("git-graph-overflow")).toHaveText("+3 elsewhere")
  })

  test("a crystallized fork's own view carries the magnifier on its anchor", async ({ page }) => {
    await page.locator(chipSelector(topo.branches.crystal)).click()

    const banner = page.getByTestId("git-panel-peeking")
    await expect(banner).toBeVisible()
    await expect(banner).toContainText("crystal")

    // The anchoring milestone X attaches at the spawning TIP milestone (M5),
    // not at the save the user forked from (fork_source ≠ fork_point).
    const anchor = page.locator(dotSelector(topo.commits.X))
    await expect(anchor).toHaveAttribute("data-lane", "0")
    await expect(anchor).toHaveAttribute("data-color-index", "1")
    await expect(anchor).toHaveAttribute("data-branch", topo.branches.crystal)

    const transition = page.locator(transitionSelector)
    await expect(transition).toHaveCount(1)
    await expect(transition).toHaveAttribute("data-from-lane", "0")
    await expect(transition).toHaveAttribute("data-to-lane", "1")

    // X is collapsed and folds the spawning branch's pre-fork pending save,
    // so its edge carries the magnifier — expanding surfaces exactly that
    // save, and the button flips to its zoom-out form.
    const magX = page.locator(magnifierSelector(topo.commits.X))
    await expect(magX).toBeVisible()
    await magX.click()
    const saves = page.getByTestId("git-panel-save")
    await expect(saves).toHaveCount(1)
    await expect(saves).toContainText("Save s1")
    await expect(magX).toHaveAttribute("data-expanded", "true")
  })

  test("the rail is inert to left-click; right-click opens the commit and lane menus", async ({
    page,
  }) => {
    // Left-clicking a dot neither selects nor expands anything.
    const dot = page.locator(dotSelector(topo.commits.M5))
    await dot.click()
    await expect(page.getByText("Loading saves…")).toHaveCount(0)
    await expect(page.getByTestId("git-panel-save")).toHaveCount(0)
    await expect(
      page.getByTestId("git-panel-milestone").filter({ hasText: "Milestone 5" }),
    ).not.toHaveAttribute("data-selected", "true")

    // Right-click on the dot: the commit actions. "View side-by-side" opens
    // the read-only comparison (a pure client view).
    await dot.dispatchEvent("contextmenu")
    await expect(page.getByTestId("git-graph-dot-menu")).toBeVisible()
    await expect(page.getByTestId("git-graph-dot-menu-view")).toBeVisible()
    await expect(page.getByTestId("git-graph-dot-menu-move")).toBeVisible()
    await page.getByTestId("git-graph-dot-menu-view").click()
    await expect(page.getByTestId("git-graph-dot-menu")).toHaveCount(0)

    // Right-click on the spine lane: the branch actions. Lane 0 is the
    // current working branch, so Switch is disabled and View is pointless —
    // both render, communicating the vocabulary.
    await page
      .locator('[data-testid="git-graph-edge"][data-edge-kind="spine"]')
      .first()
      .dispatchEvent("contextmenu")
    await expect(page.getByTestId("git-graph-lane-menu")).toBeVisible()
    await expect(page.getByTestId("git-graph-lane-menu-switch")).toBeDisabled()
  })
})
