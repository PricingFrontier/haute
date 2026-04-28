import { expect, test, type Page } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

const JOB_ID = "job-progress-render-benchmark"
const TARGET_NODE_ID = "browser_optimiser"
const MIN_STATUS_POLLS_AFTER_PROBE_START = 4
const MIN_VISIBLE_PROGRESS_TICKS = 2
// Progress polling must not make shell components perform React work. Toolbar/canvas DOM
// regions may record one bootstrap observer mutation, but repeated polling churn should fail.
const MAX_SHELL_COMPONENT_PERFORMED_WORK = 0
const MAX_TOOLBAR_DOM_MUTATIONS = 1
const MAX_CANVAS_DOM_MUTATIONS = 1

type ComponentRenderMetric = {
  renderCount: number
  performedWorkCount: number
  actualDurationCount: number
  totalActualDurationMs: number
  maxActualDurationMs: number
  flagSamples: number[]
}

type MutationSample = {
  area: string
  type: string
  target: string
  attributeName: string | null
  addedNodes: number
  removedNodes: number
}

type CommitSample = {
  atMs: number
  renderedComponents: string[]
}

type JobProgressRenderMetrics = {
  reactRendererCount: number
  reactCommitCount: number
  observedComponentNames: string[]
  componentRenders: Record<string, ComponentRenderMetric>
  domMutationCounts: {
    toolbar: number
    canvas: number
    progressPanel: number
  }
  mutationSamples: MutationSample[]
  commitSamples: CommitSample[]
}

type JobProgressRenderProbe = {
  start: () => void
  stop: () => JobProgressRenderMetrics
  snapshot: () => JobProgressRenderMetrics
}

declare global {
  interface Window {
    __hauteJobProgressRenderBenchmark?: JobProgressRenderProbe
    __REACT_DEVTOOLS_GLOBAL_HOOK__?: unknown
  }
}

type OptimiserRouteMetrics = {
  estimateRequests: number
  solveRequests: number
  statusRequests: number
  progressValues: number[]
  unexpectedRequests: string[]
}

type OptimiserRequestBody = {
  node_id?: unknown
}

async function installJobProgressRenderProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type BrowserFiber = {
      type?: unknown
      elementType?: unknown
      child?: BrowserFiber | null
      sibling?: BrowserFiber | null
      flags?: number
      actualDuration?: number
    }

    type BrowserComponentRenderMetric = {
      renderCount: number
      performedWorkCount: number
      actualDurationCount: number
      totalActualDurationMs: number
      maxActualDurationMs: number
      flagSamples: number[]
    }

    type BrowserMutationSample = {
      area: string
      type: string
      target: string
      attributeName: string | null
      addedNodes: number
      removedNodes: number
    }

    type BrowserCommitSample = {
      atMs: number
      renderedComponents: string[]
    }

    type BrowserMetrics = {
      reactRendererCount: number
      reactCommitCount: number
      observedComponentNames: string[]
      componentRenders: Record<string, BrowserComponentRenderMetric>
      domMutationCounts: {
        toolbar: number
        canvas: number
        progressPanel: number
      }
      mutationSamples: BrowserMutationSample[]
      commitSamples: BrowserCommitSample[]
    }

    const maxRenderSampleCount = 25
    const maxMutationSampleCount = 25
    const observedComponentNames = new Set<string>()
    let reactRendererCount = 0
    let recording = false
    let metrics = createMetrics()
    let observers: MutationObserver[] = []

    function isRecord(value: unknown): value is Record<string, unknown> {
      return value !== null && typeof value === "object"
    }

    function componentNameFromType(type: unknown): string | null {
      if (typeof type === "function") {
        const candidate = type as { displayName?: unknown; name?: unknown }
        if (typeof candidate.displayName === "string" && candidate.displayName.length > 0) {
          return candidate.displayName
        }
        if (typeof candidate.name === "string" && candidate.name.length > 0) {
          return candidate.name
        }
        return null
      }

      if (!isRecord(type)) return null

      const displayName = type.displayName
      if (typeof displayName === "string" && displayName.length > 0) {
        return displayName
      }

      const innerType = type.type ?? type.render
      if (innerType && innerType !== type) {
        return componentNameFromType(innerType)
      }

      return null
    }

    function componentName(fiber: BrowserFiber): string | null {
      return componentNameFromType(fiber.elementType) ?? componentNameFromType(fiber.type)
    }

    function createComponentMetric(): BrowserComponentRenderMetric {
      return {
        renderCount: 0,
        performedWorkCount: 0,
        actualDurationCount: 0,
        totalActualDurationMs: 0,
        maxActualDurationMs: 0,
        flagSamples: [],
      }
    }

    function createMetrics(): BrowserMetrics {
      return {
        reactRendererCount,
        reactCommitCount: 0,
        observedComponentNames: Array.from(observedComponentNames).sort(),
        componentRenders: {},
        domMutationCounts: {
          toolbar: 0,
          canvas: 0,
          progressPanel: 0,
        },
        mutationSamples: [],
        commitSamples: [],
      }
    }

    function cloneMetrics(): BrowserMetrics {
      return {
        ...metrics,
        observedComponentNames: Array.from(observedComponentNames).sort(),
        componentRenders: Object.fromEntries(
          Object.entries(metrics.componentRenders).map(([name, value]) => [
            name,
            {
              ...value,
              flagSamples: [...value.flagSamples],
            },
          ]),
        ),
        domMutationCounts: { ...metrics.domMutationCounts },
        mutationSamples: [...metrics.mutationSamples],
        commitSamples: metrics.commitSamples.map((sample) => ({
          ...sample,
          renderedComponents: [...sample.renderedComponents],
        })),
      }
    }

    function describeNode(node: Node): string {
      if (node.nodeType === Node.TEXT_NODE) return "#text"
      if (!(node instanceof Element)) return node.nodeName.toLowerCase()
      const id = node.id ? `#${node.id}` : ""
      const testId = node.getAttribute("data-testid")
      const testIdPart = testId ? `[data-testid="${testId}"]` : ""
      const className = typeof node.className === "string"
        ? node.className
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 3)
            .map((name) => `.${name}`)
            .join("")
        : ""
      return `${node.tagName.toLowerCase()}${id}${testIdPart}${className}`
    }

    function recordMutation(area: keyof BrowserMetrics["domMutationCounts"], records: MutationRecord[]): void {
      metrics.domMutationCounts[area] += records.length
      for (const record of records) {
        if (metrics.mutationSamples.length >= maxMutationSampleCount) return
        metrics.mutationSamples.push({
          area,
          type: record.type,
          target: describeNode(record.target),
          attributeName: record.attributeName,
          addedNodes: record.addedNodes.length,
          removedNodes: record.removedNodes.length,
        })
      }
    }

    function observeMutations(area: keyof BrowserMetrics["domMutationCounts"], selector: string): void {
      const target = document.querySelector(selector)
      if (!target) {
        throw new Error(`Job progress benchmark could not find DOM observer target: ${selector}`)
      }
      const observer = new MutationObserver((records) => recordMutation(area, records))
      observer.observe(target, {
        attributes: true,
        childList: true,
        characterData: true,
        subtree: true,
      })
      observers.push(observer)
    }

    function stopObservers(): void {
      for (const observer of observers) {
        observer.disconnect()
      }
      observers = []
    }

    function recordComponentRender(name: string, flags: number, actualDuration: number): void {
      const entry = metrics.componentRenders[name] ?? createComponentMetric()
      const performedWork = (flags & 1) === 1
      const hasActualDuration = actualDuration > 0
      entry.renderCount += 1
      if (performedWork) entry.performedWorkCount += 1
      if (hasActualDuration) entry.actualDurationCount += 1
      entry.totalActualDurationMs += actualDuration
      entry.maxActualDurationMs = Math.max(entry.maxActualDurationMs, actualDuration)
      if (entry.flagSamples.length < 5) {
        entry.flagSamples.push(flags)
      }
      metrics.componentRenders[name] = entry
    }

    function visitFibers(root: BrowserFiber, renderedComponents: Set<string> | null): void {
      let fiber: BrowserFiber | null | undefined = root
      while (fiber) {
        const name = componentName(fiber)
        if (name) {
          observedComponentNames.add(name)
          const flags = typeof fiber.flags === "number" ? fiber.flags : 0
          const actualDuration =
            typeof fiber.actualDuration === "number" && Number.isFinite(fiber.actualDuration)
              ? fiber.actualDuration
              : 0
          const performedWork = (flags & 1) === 1
          const hasRenderEvidence = (flags & 1) === 1 || actualDuration > 0
          if (recording && hasRenderEvidence) {
            recordComponentRender(name, flags, actualDuration)
            if (renderedComponents && performedWork) {
              renderedComponents.add(name)
            }
          }
        }

        if (fiber.child) {
          visitFibers(fiber.child, renderedComponents)
        }
        fiber = fiber.sibling
      }
    }

    if (window.__REACT_DEVTOOLS_GLOBAL_HOOK__) {
      throw new Error("Job progress benchmark requires ownership of __REACT_DEVTOOLS_GLOBAL_HOOK__")
    }

    window.__hauteJobProgressRenderBenchmark = {
      start: () => {
        stopObservers()
        metrics = createMetrics()
        recording = true
        observeMutations("toolbar", '[role="toolbar"][aria-label="Pipeline toolbar"]')
        observeMutations("canvas", ".react-flow")
        observeMutations("progressPanel", 'aside[aria-label="Node properties"]')
      },
      stop: () => {
        recording = false
        stopObservers()
        return cloneMetrics()
      },
      snapshot: () => cloneMetrics(),
    }

    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
      supportsFiber: true,
      renderers: new Map(),
      inject(renderer: unknown) {
        reactRendererCount += 1
        metrics.reactRendererCount = reactRendererCount
        const renderers = (this as { renderers: Map<number, unknown> }).renderers
        renderers.set(reactRendererCount, renderer)
        return reactRendererCount
      },
      onCommitFiberRoot(_rendererId: number, root: { current?: BrowserFiber }) {
        if (root.current) {
          const renderedComponents = recording ? new Set<string>() : null
          visitFibers(root.current, renderedComponents)
          if (recording) {
            metrics.reactCommitCount += 1
            if (metrics.commitSamples.length < maxRenderSampleCount) {
              metrics.commitSamples.push({
                atMs: performance.now(),
                renderedComponents: Array.from(renderedComponents ?? []).sort().slice(0, 50),
              })
            }
          }
        }
      },
      onCommitFiberUnmount() {},
    }
  })
}

async function installOptimiserBenchmarkRoutes(page: Page): Promise<{
  metrics: () => OptimiserRouteMetrics
}> {
  const routeMetrics: OptimiserRouteMetrics = {
    estimateRequests: 0,
    solveRequests: 0,
    statusRequests: 0,
    progressValues: [],
    unexpectedRequests: [],
  }

  await page.route("**/api/optimiser/**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const method = request.method()

    if (method === "POST" && url.pathname === "/api/optimiser/estimate") {
      routeMetrics.estimateRequests += 1
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ total_rows: 5_000 }),
      })
      return
    }

    if (method === "POST" && url.pathname === "/api/optimiser/solve") {
      routeMetrics.solveRequests += 1
      const body = request.postDataJSON() as OptimiserRequestBody
      if (body.node_id !== TARGET_NODE_ID) {
        throw new Error(`Expected optimiser solve for ${TARGET_NODE_ID}, received ${String(body.node_id)}`)
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "started",
          job_id: JOB_ID,
          error: null,
        }),
      })
      return
    }

    if (method === "GET" && url.pathname === `/api/optimiser/solve/status/${JOB_ID}`) {
      routeMetrics.statusRequests += 1
      const tick = routeMetrics.statusRequests
      const progress = Math.min(0.95, tick * 0.11)
      routeMetrics.progressValues.push(progress)
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "running",
          progress,
          message: `Benchmark progress #${tick}.`,
          elapsed_seconds: tick * 0.5,
          result: buildBroadRunningSolveResult(tick),
        }),
      })
      return
    }

    const unexpected = `${method} ${url.pathname}`
    routeMetrics.unexpectedRequests.push(unexpected)
    throw new Error(`Unexpected optimiser benchmark request: ${unexpected}`)
  })

  return {
    metrics: () => ({
      estimateRequests: routeMetrics.estimateRequests,
      solveRequests: routeMetrics.solveRequests,
      statusRequests: routeMetrics.statusRequests,
      progressValues: [...routeMetrics.progressValues],
      unexpectedRequests: [...routeMetrics.unexpectedRequests],
    }),
  }
}

function buildBroadRunningSolveResult(tick: number): Record<string, unknown> {
  return {
    status: "running",
    mode: "online",
    total_objective: 1_000 + tick,
    baseline_objective: 900,
    constraints: { volume: 0.9 + tick / 1_000 },
    baseline_constraints: { volume: 1 },
    lambdas: { volume: tick / 100 },
    converged: false,
    iterations: tick,
    n_quotes: 5_000,
    n_steps: 20,
    cd_iterations: tick,
    warning: null,
    scenario_value_stats: {
      mean: 1,
      std: 0.1,
      min: 0.8,
      max: 1.2,
      p5: 0.85,
      p25: 0.95,
      p50: 1,
      p75: 1.05,
      p95: 1.15,
      pct_increase: 0.4,
      pct_decrease: 0.35,
    },
    history: Array.from({ length: 24 }, (_, index) => ({
      iteration: tick * 100 + index,
      total_objective: 1_000 + tick + index,
      max_lambda_change: 1 / (tick + index + 1),
      all_constraints_satisfied: false,
      lambdas: { volume: (tick + index) / 100 },
      total_constraints: { volume: 0.9 + (tick + index) / 10_000 },
    })),
  }
}

async function probeSnapshot(page: Page): Promise<JobProgressRenderMetrics> {
  return page.evaluate(() => {
    const probe = window.__hauteJobProgressRenderBenchmark
    if (!probe) throw new Error("Job progress benchmark probe was not installed")
    return probe.snapshot()
  })
}

async function startProbe(page: Page): Promise<void> {
  await page.evaluate(() => {
    const probe = window.__hauteJobProgressRenderBenchmark
    if (!probe) throw new Error("Job progress benchmark probe was not installed")
    probe.start()
  })
}

async function stopProbe(page: Page): Promise<JobProgressRenderMetrics> {
  return page.evaluate(() => {
    const probe = window.__hauteJobProgressRenderBenchmark
    if (!probe) throw new Error("Job progress benchmark probe was not installed")
    return probe.stop()
  })
}

async function visibleProgressTick(page: Page): Promise<number> {
  return page.locator('aside[aria-label="Node properties"]').evaluate((panel) => {
    const match = panel.textContent?.match(/Benchmark progress #(\d+)\./)
    return match ? Number(match[1]) : 0
  })
}

function componentPerformedWorkCount(metrics: JobProgressRenderMetrics, name: string): number {
  return metrics.componentRenders[name]?.performedWorkCount ?? 0
}

test.describe("job progress render benchmark", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("@benchmark keeps job progress polling out of the full editor shell render path", async ({
    page,
  }) => {
    await installJobProgressRenderProbe(page)
    const optimiserRoutes = await installOptimiserBenchmarkRoutes(page)

    await page.goto("/")

    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await expect
      .poll(async () => (await probeSnapshot(page)).reactRendererCount, {
        message: "React should inject into the preloaded benchmark DevTools hook",
      })
      .toBeGreaterThan(0)

    const optimiserNode = page.getByLabel("Optimisation node: browser_optimiser")
    await expect(optimiserNode).toBeVisible()
    await optimiserNode.click()

    await expect(page.getByRole("button", { name: "Optimise", exact: true })).toBeEnabled()
    await page.getByRole("button", { name: "Optimise", exact: true }).click()

    await expect
      .poll(() => optimiserRoutes.metrics().statusRequests, {
        message: "benchmark should observe the initial polling tick before measuring steady state",
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(1)
    await expect
      .poll(() => visibleProgressTick(page), {
        message: "initial progress should be visible before starting render isolation measurements",
      })
      .toBeGreaterThanOrEqual(1)

    const initialProgressTick = await visibleProgressTick(page)
    const initialStatusRequests = optimiserRoutes.metrics().statusRequests
    await startProbe(page)

    await expect
      .poll(() => optimiserRoutes.metrics().statusRequests, {
        message: "benchmark should exercise multiple polling ticks after probe start",
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(initialStatusRequests + MIN_STATUS_POLLS_AFTER_PROBE_START)
    await expect
      .poll(() => visibleProgressTick(page), {
        message: "throttled progress UI should receive visible updates during the benchmark window",
        timeout: 15_000,
      })
      .toBeGreaterThanOrEqual(initialProgressTick + MIN_VISIBLE_PROGRESS_TICKS)

    const result = await stopProbe(page)
    const routeMetrics = optimiserRoutes.metrics()
    const summary = JSON.stringify(
      {
        jobId: JOB_ID,
        targetNodeId: TARGET_NODE_ID,
        routeMetrics,
        visibleProgress: {
          initialProgressTick,
          latestProgressTick: await visibleProgressTick(page),
          minVisibleProgressTicks: MIN_VISIBLE_PROGRESS_TICKS,
        },
        budgets: {
          shellComponentPerformedWorkCount: MAX_SHELL_COMPONENT_PERFORMED_WORK,
          toolbarDomMutations: MAX_TOOLBAR_DOM_MUTATIONS,
          canvasDomMutations: MAX_CANVAS_DOM_MUTATIONS,
        },
        result,
      },
      null,
      2,
    )
    await test.info().attach("job-progress-render-metrics.json", {
      body: summary,
      contentType: "application/json",
    })
    console.info(`job progress render benchmark metrics:\n${summary}`)

    expect(routeMetrics.unexpectedRequests, `unexpected optimiser requests:\n${summary}`).toEqual([])
    expect(routeMetrics.solveRequests, `solve route was not exercised:\n${summary}`).toBe(1)
    expect(routeMetrics.statusRequests, `status polling route was not exercised:\n${summary}`).toBeGreaterThanOrEqual(
      initialStatusRequests + MIN_STATUS_POLLS_AFTER_PROBE_START,
    )
    expect(
      routeMetrics.progressValues.length,
      `progress responses were not recorded:\n${summary}`,
    ).toBeGreaterThanOrEqual(routeMetrics.statusRequests)
    expect(
      await visibleProgressTick(page),
      `visible progress did not advance enough:\n${summary}`,
    ).toBeGreaterThanOrEqual(initialProgressTick + MIN_VISIBLE_PROGRESS_TICKS)
    expect(
      result.domMutationCounts.progressPanel,
      `progress UI should mutate while polling progresses:\n${summary}`,
    ).toBeGreaterThan(0)
    expect(
      componentPerformedWorkCount(result, "BackgroundJobPolling"),
      `BackgroundJobPolling should perform work while polling progresses:\n${summary}`,
    ).toBeGreaterThan(0)
    expect(
      componentPerformedWorkCount(result, "OptimiserConfig"),
      `OptimiserConfig should perform work while visible progress updates:\n${summary}`,
    ).toBeGreaterThan(0)
    expect(result.observedComponentNames, `React probe did not observe FlowEditor:\n${summary}`).toContain(
      "FlowEditor",
    )
    expect(result.observedComponentNames, `React probe did not observe Toolbar:\n${summary}`).toContain(
      "Toolbar",
    )
    expect(
      componentPerformedWorkCount(result, "FlowEditor"),
      `FlowEditor performed work during job progress polling:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_SHELL_COMPONENT_PERFORMED_WORK)
    expect(
      componentPerformedWorkCount(result, "Toolbar"),
      `Toolbar performed work during job progress polling:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_SHELL_COMPONENT_PERFORMED_WORK)
    expect(
      componentPerformedWorkCount(result, "ReactFlow"),
      `ReactFlow performed work during job progress polling:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_SHELL_COMPONENT_PERFORMED_WORK)
    expect(
      result.domMutationCounts.toolbar,
      `toolbar DOM mutated during job progress polling:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_TOOLBAR_DOM_MUTATIONS)
    expect(
      result.domMutationCounts.canvas,
      `canvas DOM mutated during job progress polling:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_CANVAS_DOM_MUTATIONS)
  })
})
