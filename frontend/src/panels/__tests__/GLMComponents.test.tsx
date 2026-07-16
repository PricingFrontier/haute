/**
 * Tests for the GLM-specific UI components:
 *   - GLMTargetConfig (family, link, offset, metrics)
 *   - GLMFactorConfig (visual builder, interactions, JSON sync)
 *   - GLMRegularizationConfig (type toggle, alpha, CV folds, L1 ratio)
 *   - GLMCoefficientsTab (sortable coefficients table)
 *   - GLMRelativitiesTab (bar chart with sort modes)
 *   - ModellingConfig GLM routing (algorithm picker, GLM panel rendering)
 *   - SummaryTab GLM fit statistics
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"
import { GLMTargetConfig } from "../modelling/GLMTargetConfig"
import { GLMFactorConfig } from "../modelling/GLMFactorConfig"
import { GLMRegularizationConfig } from "../modelling/GLMRegularizationConfig"
import { GLMCoefficientsTab } from "../modelling/GLMCoefficientsTab"
import { GLMRelativitiesTab } from "../modelling/GLMRelativitiesTab"
import { SummaryTab } from "../modelling/SummaryTab"
import ModellingConfig from "../ModellingConfig"
import { GraphProvider } from "../GraphContext"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { TrainResult } from "../../stores/useNodeResultsStore"

// ── Mocks ────────────────────────────────────────────────────────

vi.mock("../../api/client", () => ({
  trainModel: vi.fn(() => new Promise(() => {})),
  estimateTrainingRam: vi.fn(() => new Promise(() => {})),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

vi.mock("../modelling/TrainingProgress", () => ({
  TrainingProgress: () => <div data-testid="training-progress" />,
}))

vi.mock("../modelling/MlflowExportSection", () => ({
  MlflowExportSection: () => <div data-testid="mlflow-export" />,
}))

// ── Shared helpers ───────────────────────────────────────────────

const defaultColumns = [
  { name: "claim_count", dtype: "Int64" },
  { name: "age", dtype: "Float64" },
  { name: "region", dtype: "Utf8" },
  { name: "exposure", dtype: "Float64" },
  { name: "severity", dtype: "Float64" },
]

function makeGlmCoefficients() {
  return [
    { feature: "(Intercept)", coefficient: -1.234, std_error: 0.05, z_value: -24.68, p_value: 0.0001, significance: "***" },
    { feature: "age", coefficient: 0.012, std_error: 0.003, z_value: 4.0, p_value: 0.0001, significance: "***" },
    { feature: "region_B", coefficient: 0.156, std_error: 0.08, z_value: 1.95, p_value: 0.051, significance: "." },
    { feature: "region_C", coefficient: -0.089, std_error: 0.09, z_value: -0.99, p_value: 0.322, significance: "" },
  ]
}

function makeGlmRelativities() {
  return [
    { feature: "(Intercept)", relativity: 0.291, ci_lower: 0.265, ci_upper: 0.32 },
    { feature: "age", relativity: 1.012, ci_lower: 1.006, ci_upper: 1.018 },
    { feature: "region_B", relativity: 1.169, ci_lower: 0.998, ci_upper: 1.37 },
    { feature: "region_C", relativity: 0.915, ci_lower: 0.766, ci_upper: 1.093 },
  ]
}

function makeTrainResult(overrides: Partial<TrainResult> = {}): TrainResult {
  return {
    status: "ok",
    metrics: { gini: 0.35, poisson_deviance: 1.42 },
    feature_importance: [
      { feature: "age", importance: 0.6 },
      { feature: "region", importance: 0.4 },
    ],
    model_path: "/models/glm_model.rsglm",
    train_rows: 8000,
    test_rows: 2000,
    ...overrides,
  }
}

beforeEach(() => {
  useNodeResultsStore.setState({ trainJobs: {}, trainResults: {} })
  useSettingsStore.setState({
    mlflow: {
      status: "pending",
      backend: "",
      host: "",
      installed: null,
      importable: null,
      trackingConfigured: null,
      detail: "",
    },
    openSections: {},
  })
})

afterEach(cleanup)

// ═════════════════════════════════════════════════════════════════
// GLMTargetConfig
// ═════════════════════════════════════════════════════════════════

describe("GLMTargetConfig", () => {
  const baseConfig = { _nodeId: "n1", algorithm: "glm", target: "claim_count", weight: "exposure", family: "poisson" }
  const onUpdate = vi.fn()

  beforeEach(() => onUpdate.mockReset())

  it("renders target and weight dropdowns", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText("Target column")).toBeTruthy()
    expect(screen.getByText("Weight column (optional)")).toBeTruthy()
  })

  it("renders all backend-accepted family buttons", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    // Every family the backend _VALID_GLM_LINKS validates, incl. Quasi-Poisson
    // (dispersion estimated from Pearson residuals — no user parameter).
    for (const label of [
      "Poisson", "Gamma", "Tweedie", "Gaussian", "Binomial", "Quasi-Poisson",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy()
    }
    // Neg. Binomial is held: its dispersion `theta` fits silently at 1.0 with
    // no gate yet, so the backend rejects it and the UI must not offer it.
    expect(screen.queryByRole("button", { name: "Neg. Binomial" })).toBeNull()
  })

  it("selecting Quasi-Poisson sets family, canonical link and count metrics", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "Quasi-Poisson" }))
    expect(onUpdate).toHaveBeenCalledWith({
      family: "quasipoisson",
      link: "",
      metrics: ["gini", "poisson_deviance"],
    })
  })

  it("clicking a family updates family, link, and metrics", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "Gamma" }))
    expect(onUpdate).toHaveBeenCalledWith({
      family: "gamma",
      link: "",
      metrics: ["gini", "rmse"],
    })
  })

  it("shows Tweedie variance power slider only when family=tweedie", () => {
    const { unmount } = render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.queryByText(/Variance power/)).toBeNull()
    unmount()

    render(<GLMTargetConfig config={{ ...baseConfig, family: "tweedie" }} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText(/Variance power/)).toBeTruthy()
  })

  it("renders link function buttons with auto default", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByRole("button", { name: /auto \(log\)/ })).toBeTruthy()
  })

  it("clicking non-canonical link sets link override", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "identity" }))
    expect(onUpdate).toHaveBeenCalledWith("link", "identity")
  })

  it("renders offset column dropdown with link-function help", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText(/Offset column/)).toBeTruthy()
    // The link-function framing is carried by the hover help.
    expect(screen.getByTestId("offset-help")).toBeTruthy()
  })

  it("renders intercept checkbox checked by default", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    const checkbox = screen.getByRole("checkbox")
    expect(checkbox).toBeTruthy()
    expect((checkbox as HTMLInputElement).checked).toBe(true)
  })

  it("unchecking intercept calls onUpdate", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("checkbox"))
    expect(onUpdate).toHaveBeenCalledWith("intercept", false)
  })

  it("renders GLM metrics toggle buttons", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByRole("button", { name: "Gini" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "RMSE" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Poisson Dev." })).toBeTruthy()
    expect(screen.getByRole("button", { name: "R²" })).toBeTruthy()
  })

  it("clicking a metric toggles it in/out", () => {
    const config = { ...baseConfig, metrics: ["gini", "poisson_deviance"] }
    render(<GLMTargetConfig config={config} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "RMSE" }))
    expect(onUpdate).toHaveBeenCalledWith("metrics", ["gini", "poisson_deviance", "rmse"])
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMFactorConfig
// ═════════════════════════════════════════════════════════════════

describe("GLMFactorConfig", () => {
  const baseProps = {
    config: { _nodeId: "n1", target: "claim_count", weight: "exposure", algorithm: "glm" },
    onUpdate: vi.fn(),
    columns: defaultColumns,
    target: "claim_count",
    weight: "exposure",
    exclude: [] as string[],
  }

  // Config with some factors already added
  const withFactors = {
    ...baseProps,
    config: {
      ...baseProps.config,
      terms: {
        age: { type: "linear" },
        region: { type: "categorical" },
      },
    },
  }

  beforeEach(() => baseProps.onUpdate.mockReset())

  // ── Empty state ──

  it("starts with zero factors by default", () => {
    render(<GLMFactorConfig {...baseProps} />)
    expect(screen.getByText("(0)")).toBeTruthy()
    expect(screen.getByText("No factors added yet. Add columns below to include them in the model.")).toBeTruthy()
  })

  it("shows 'Add factor...' dropdown with eligible columns", () => {
    render(<GLMFactorConfig {...baseProps} />)
    // The add dropdown should list age, region, severity (not claim_count or exposure)
    const addSelect = screen.getByDisplayValue("Add factor...")
    const options = within(addSelect as HTMLElement).getAllByRole("option")
    const optionTexts = options.map(o => o.textContent)
    expect(optionTexts).toContain("age (Float64)")
    expect(optionTexts).toContain("region (Utf8)")
    expect(optionTexts).toContain("severity (Float64)")
    // target and weight excluded
    expect(optionTexts.some(t => t?.startsWith("claim_count"))).toBe(false)
    expect(optionTexts.some(t => t?.startsWith("exposure"))).toBe(false)
  })

  // ── Adding factors ──

  it("selecting from dropdown adds a factor with smart default type", () => {
    render(<GLMFactorConfig {...baseProps} />)
    const addSelect = screen.getByDisplayValue("Add factor...")
    fireEvent.change(addSelect, { target: { value: "region" } })
    // region is Utf8 → should default to categorical
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", {
      region: { type: "categorical" },
    })
  })

  it("selecting numeric column defaults to linear", () => {
    render(<GLMFactorConfig {...baseProps} />)
    const addSelect = screen.getByDisplayValue("Add factor...")
    fireEvent.change(addSelect, { target: { value: "age" } })
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "linear" },
    })
  })

  // ── Displaying added factors ──

  it("shows added factors as configurable rows", () => {
    render(<GLMFactorConfig {...withFactors} />)
    expect(screen.getByText("age")).toBeTruthy()
    expect(screen.getByText("region")).toBeTruthy()
    expect(screen.getByText("(2)")).toBeTruthy()
    // severity not added → should not appear as a factor row
    // (it appears in the add dropdown, but not as an active factor label)
    const factorLabels = Array.from(document.querySelectorAll(".font-mono.truncate")).map(el => el.textContent)
    expect(factorLabels).not.toContain("severity")
  })

  it("dropdown only shows remaining (un-added) columns", () => {
    render(<GLMFactorConfig {...withFactors} />)
    const addSelect = screen.getByDisplayValue("Add factor...")
    const options = within(addSelect as HTMLElement).getAllByRole("option")
    const optionTexts = options.map(o => o.textContent)
    // age and region already added → only severity in dropdown
    expect(optionTexts).toContain("severity (Float64)")
    expect(optionTexts.some(t => t?.startsWith("age"))).toBe(false)
    expect(optionTexts.some(t => t?.startsWith("region"))).toBe(false)
  })

  it("hides add controls when all columns are added", () => {
    const config = {
      ...baseProps.config,
      terms: {
        age: { type: "linear" },
        region: { type: "categorical" },
        severity: { type: "linear" },
      },
    }
    render(<GLMFactorConfig {...baseProps} config={config} />)
    // No more columns to add → dropdown should not be present
    expect(screen.queryByDisplayValue("Add factor...")).toBeNull()
  })

  // ── Removing factors ──

  it("each factor row has a remove button", () => {
    render(<GLMFactorConfig {...withFactors} />)
    const removeBtns = screen.getAllByTitle(/^Remove /)
    expect(removeBtns.length).toBe(2) // age and region
  })

  it("clicking remove on a factor removes it from terms", () => {
    render(<GLMFactorConfig {...withFactors} />)
    const removeAge = screen.getByTitle("Remove age")
    fireEvent.click(removeAge)
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", {
      region: { type: "categorical" },
    })
  })

  // ── Factor configuration ──

  it("changing term type updates config", () => {
    render(<GLMFactorConfig {...withFactors} />)
    const ageRow = screen.getByText("age").closest("div")!
    const select = ageRow.querySelector("select")!
    fireEvent.change(select, { target: { value: "bs" } })
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", expect.objectContaining({
      age: { type: "bs" },
    }))
  })

  it("spline type shows df input", () => {
    const config = { ...baseProps.config, terms: { age: { type: "bs", df: 5 } } }
    render(<GLMFactorConfig {...baseProps} config={config} />)
    const dfInput = document.querySelector('input[placeholder="df"]') as HTMLInputElement
    expect(dfInput).toBeTruthy()
    expect(dfInput.value).toBe("5")
  })

  it("monotonicity toggle cycles through states", () => {
    render(<GLMFactorConfig {...withFactors} />)
    const ageRow = screen.getByText("age").closest("div")!
    const monoBtn = within(ageRow).getByTitle(/No constraint/)
    expect(monoBtn.textContent).toBe("─")

    fireEvent.click(monoBtn)
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", expect.objectContaining({
      age: expect.objectContaining({ monotonicity: "increasing" }),
    }))
  })

  // ── Interactions ──

  it("interactions section has Add button", () => {
    render(<GLMFactorConfig {...baseProps} />)
    expect(screen.getByText("Interactions")).toBeTruthy()
    expect(screen.getByText("Add")).toBeTruthy()
  })

  it("clicking Add creates an interaction row", () => {
    render(<GLMFactorConfig {...baseProps} />)
    fireEvent.click(screen.getByText("Add"))
    expect(baseProps.onUpdate).toHaveBeenCalledWith("interactions", [
      { factors: ["", ""], include_main: true },
    ])
  })

  it("interaction dropdowns only list added factors", () => {
    const config = {
      ...withFactors.config,
      interactions: [{ factors: ["", ""], include_main: true }],
    }
    render(<GLMFactorConfig {...withFactors} config={config} />)
    // Find the interaction dropdown by its "Select..." placeholder
    const interactionSelects = screen.getAllByDisplayValue("Select...")
    expect(interactionSelects.length).toBe(2)
    const options = within(interactionSelects[0] as HTMLElement).getAllByRole("option")
    const optionTexts = options.map(o => o.textContent)
    expect(optionTexts).toContain("age")
    expect(optionTexts).toContain("region")
    expect(optionTexts).not.toContain("severity")
  })

  it("interaction row shows two dropdowns and main checkbox", () => {
    const config = {
      ...withFactors.config,
      interactions: [{ factors: ["age", "region"], include_main: true }],
    }
    render(<GLMFactorConfig {...withFactors} config={config} />)
    expect(screen.getByText("x")).toBeTruthy()
    const checkboxes = screen.getAllByRole("checkbox")
    expect(checkboxes.length).toBeGreaterThanOrEqual(1)
  })

  it("removing interaction calls onUpdate", () => {
    const config = {
      ...withFactors.config,
      interactions: [{ factors: ["age", "region"], include_main: true }],
    }
    render(<GLMFactorConfig {...withFactors} config={config} />)
    const removeBtn = screen.getByTitle("Remove interaction")
    fireEvent.click(removeBtn)
    expect(baseProps.onUpdate).toHaveBeenCalledWith("interactions", [])
  })

  // ── Builder / JSON mode toggle ──

  it("shows Builder and JSON mode tabs", () => {
    render(<GLMFactorConfig {...baseProps} />)
    expect(screen.getByRole("button", { name: "Builder" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "JSON" })).toBeTruthy()
  })

  it("defaults to Builder mode", () => {
    render(<GLMFactorConfig {...baseProps} />)
    expect(screen.getByText("No factors added yet. Add columns below to include them in the model.")).toBeTruthy()
    expect(document.querySelector("textarea")).toBeNull()
  })

  it("switching to JSON mode shows textarea", () => {
    render(<GLMFactorConfig {...baseProps} />)
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    expect(screen.getByText(/RustyStats terms dict/)).toBeTruthy()
    expect(document.querySelector("textarea")).toBeTruthy()
  })

  it("JSON textarea reflects only added factors (empty when none)", () => {
    render(<GLMFactorConfig {...baseProps} />)
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const textarea = document.querySelector("textarea") as HTMLTextAreaElement
    expect(JSON.parse(textarea.value)).toEqual({})
  })

  it("JSON textarea reflects added factors", () => {
    render(<GLMFactorConfig {...withFactors} />)
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const textarea = document.querySelector("textarea") as HTMLTextAreaElement
    const parsed = JSON.parse(textarea.value)
    expect(parsed).toHaveProperty("age")
    expect(parsed).toHaveProperty("region")
    expect(parsed).not.toHaveProperty("severity")
  })

  it("editing and blurring JSON commits terms to config", () => {
    render(<GLMFactorConfig {...baseProps} />)
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const textarea = document.querySelector("textarea")!
    fireEvent.change(textarea, { target: { value: '{"age": {"type": "ns", "df": 4}}' } })
    fireEvent.blur(textarea)
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", { age: { type: "ns", df: 4 } })
  })

  it("pasting JSON from Atelier adds those factors", () => {
    render(<GLMFactorConfig {...baseProps} />)
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const textarea = document.querySelector("textarea")!
    const atelierJson = JSON.stringify({
      age: { type: "bs", df: 5 },
      region: { type: "categorical" },
      severity: { type: "ns", df: 3, monotonicity: "increasing" },
    })
    fireEvent.change(textarea, { target: { value: atelierJson } })
    fireEvent.blur(textarea)
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", {
      age: { type: "bs", df: 5 },
      region: { type: "categorical" },
      severity: { type: "ns", df: 3, monotonicity: "increasing" },
    })
  })

  it("invalid JSON shows error indicator", () => {
    render(<GLMFactorConfig {...baseProps} />)
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const textarea = document.querySelector("textarea")!
    fireEvent.change(textarea, { target: { value: "{bad" } })
    fireEvent.blur(textarea)
    expect(textarea.style.border).toContain("var(--danger)")
  })

  it("switching back to Builder after JSON edit shows updated factors", () => {
    render(<GLMFactorConfig {...baseProps} />)
    // Switch to JSON, paste terms, blur to commit
    fireEvent.click(screen.getByRole("button", { name: "JSON" }))
    const textarea = document.querySelector("textarea")!
    fireEvent.change(textarea, { target: { value: '{"age": {"type": "linear"}}' } })
    fireEvent.blur(textarea)
    expect(baseProps.onUpdate).toHaveBeenCalledWith("terms", { age: { type: "linear" } })
  })

  it("mode tabs are hidden when section is collapsed", () => {
    render(<GLMFactorConfig {...baseProps} />)
    // Collapse
    fireEvent.click(screen.getByText(/Factors/))
    expect(screen.queryByRole("button", { name: "Builder" })).toBeNull()
    expect(screen.queryByRole("button", { name: "JSON" })).toBeNull()
  })

  // ── Exclude ──

  it("excluded columns are not available in add dropdown", () => {
    render(<GLMFactorConfig {...baseProps} exclude={["age"]} />)
    const addSelect = screen.getByDisplayValue("Add factor...")
    const options = within(addSelect as HTMLElement).getAllByRole("option")
    const optionTexts = options.map(o => o.textContent)
    expect(optionTexts.some(t => t?.startsWith("age"))).toBe(false)
    expect(optionTexts).toContain("region (Utf8)")
    expect(optionTexts).toContain("severity (Float64)")
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMRegularizationConfig
// ═════════════════════════════════════════════════════════════════

describe("GLMRegularizationConfig", () => {
  const onUpdate = vi.fn()

  beforeEach(() => onUpdate.mockReset())

  it("renders collapsed by default", () => {
    render(<GLMRegularizationConfig config={{}} onUpdate={onUpdate} />)
    expect(screen.getByText("Regularization")).toBeTruthy()
    // Type buttons should NOT be visible (collapsed)
    expect(screen.queryByRole("button", { name: "None" })).toBeNull()
  })

  it("expanding shows type toggle buttons", () => {
    render(<GLMRegularizationConfig config={{}} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    expect(screen.getByRole("button", { name: "None" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Ridge" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Lasso" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Elastic Net" })).toBeTruthy()
  })

  it("clicking Ridge sets regularization", () => {
    render(<GLMRegularizationConfig config={{}} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    fireEvent.click(screen.getByRole("button", { name: "Ridge" }))
    expect(onUpdate).toHaveBeenCalledWith("regularization", "ridge")
  })

  it("clicking None clears regularization", () => {
    render(<GLMRegularizationConfig config={{ regularization: "ridge" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    fireEvent.click(screen.getByRole("button", { name: "None" }))
    expect(onUpdate).toHaveBeenCalledWith("regularization", null)
  })

  it("shows alpha input when regularization is active", () => {
    render(<GLMRegularizationConfig config={{ regularization: "ridge" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    expect(screen.getByText(/Alpha/)).toBeTruthy()
  })

  it("L1 ratio controls only appear for elastic_net", () => {
    const { unmount } = render(<GLMRegularizationConfig config={{ regularization: "ridge" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    expect(screen.queryByText(/L1 ratio/)).toBeNull()
    unmount()

    render(<GLMRegularizationConfig config={{ regularization: "elastic_net" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    expect(screen.getAllByText(/L1 ratio/).length).toBeGreaterThan(0)
  })

  it("elastic_net gates the L1 ratio until it is set explicitly", () => {
    render(<GLMRegularizationConfig config={{ regularization: "elastic_net" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    // Unset: the mix must be chosen — a prompt and both collapse shortcuts.
    expect(screen.getByRole("button", { name: /Set L1 ratio mix/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Fit Ridge \(0\)/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Fit LASSO \(1\)/ })).toBeTruthy()
  })

  it("Fit Ridge / Fit LASSO shortcuts set an explicit L1 ratio", () => {
    render(<GLMRegularizationConfig config={{ regularization: "elastic_net" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    fireEvent.click(screen.getByRole("button", { name: /Fit Ridge \(0\)/ }))
    expect(onUpdate).toHaveBeenCalledWith("l1_ratio", 0)
    fireEvent.click(screen.getByRole("button", { name: /Fit LASSO \(1\)/ }))
    expect(onUpdate).toHaveBeenCalledWith("l1_ratio", 1)
  })

  it("a set L1 ratio shows the mix slider, not the prompt", () => {
    render(<GLMRegularizationConfig config={{ regularization: "elastic_net", l1_ratio: 0.3 }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByText("Regularization"))
    expect(screen.queryByRole("button", { name: /Set L1 ratio mix/ })).toBeNull()
    expect(screen.getByText("0.30")).toBeTruthy()
  })

  it("shows active badge when regularization is set", () => {
    render(<GLMRegularizationConfig config={{ regularization: "lasso" }} onUpdate={onUpdate} />)
    expect(screen.getByText("lasso")).toBeTruthy()
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMCoefficientsTab
// ═════════════════════════════════════════════════════════════════

describe("GLMCoefficientsTab", () => {
  it("renders coefficient table with correct columns", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText(/^Term/)).toBeTruthy()
    expect(screen.getByText(/^Estimate/)).toBeTruthy()
    expect(screen.getByText(/^Std\. Error/)).toBeTruthy()
    // "z" column — use the th element directly
    const headers = document.querySelectorAll("th")
    const headerTexts = Array.from(headers).map(h => h.textContent!.trim())
    expect(headerTexts).toContain("z")
    expect(headerTexts.some(h => h.startsWith("Pr(>|z|)"))).toBe(true)
    expect(screen.getByText("Sig.")).toBeTruthy()
  })

  it("renders all coefficient rows", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("(Intercept)")).toBeTruthy()
    expect(screen.getByText("age")).toBeTruthy()
    expect(screen.getByText("region_B")).toBeTruthy()
    expect(screen.getByText("region_C")).toBeTruthy()
  })

  it("shows significance stars", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    // There should be *** entries and . entries
    const stars = screen.getAllByText("***")
    expect(stars.length).toBe(2) // intercept and age
    expect(screen.getByText(".")).toBeTruthy()
  })

  it("shows significance legend", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("Signif. codes:")).toBeTruthy()
    expect(screen.getByText("4 terms")).toBeTruthy()
  })

  it("clicking column header sorts data", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    // Default sort: by p_value asc. Click "Term" to sort by name
    fireEvent.click(screen.getByText("Term"))
    const rows = document.querySelectorAll("tbody tr")
    const firstCell = rows[0].querySelector("td")!.textContent
    expect(firstCell).toBe("(Intercept)")
  })

  it("clicking same column header reverses sort direction", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    // Click "Estimate" twice — after first click the text includes sort indicator
    fireEvent.click(screen.getByText(/^Estimate/))
    fireEvent.click(screen.getByText(/^Estimate/))
    // Should now be descending (largest first)
    const rows = document.querySelectorAll("tbody tr")
    const firstVal = rows[0].querySelectorAll("td")[1].textContent
    // region_B has 0.156 — the largest
    expect(firstVal).toBe("0.156000")
  })

  it("shows empty state when no coefficients", () => {
    const result = makeTrainResult({ glm_coefficients: [] })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("No coefficient data available")).toBeTruthy()
  })

  it("formats very small p-values in scientific notation", () => {
    const result = makeTrainResult({
      glm_coefficients: [
        { feature: "test", coefficient: 1, std_error: 0.001, z_value: 1000, p_value: 0.00001, significance: "***" },
      ],
    })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("1.00e-5")).toBeTruthy()
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMRelativitiesTab
// ═════════════════════════════════════════════════════════════════

describe("GLMRelativitiesTab", () => {
  it("renders relativity bars for all terms", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("(Intercept)")).toBeTruthy()
    expect(screen.getByText("age")).toBeTruthy()
    expect(screen.getByText("region_B")).toBeTruthy()
    expect(screen.getByText("region_C")).toBeTruthy()
  })

  it("shows sort mode buttons", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByRole("button", { name: "By deviation" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "By value" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "A–Z" })).toBeTruthy()
  })

  it("default sort is by deviation (largest first)", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    // Intercept has relativity 0.291, so deviation |0.291 - 1| = 0.709 is largest
    const firstLabel = document.querySelectorAll(".truncate")[0].textContent
    expect(firstLabel).toBe("(Intercept)")
  })

  it("clicking A–Z sorts alphabetically", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    fireEvent.click(screen.getByRole("button", { name: "A–Z" }))
    const labels = Array.from(document.querySelectorAll(".truncate")).map(el => el.textContent)
    expect(labels[0]).toBe("(Intercept)")
    expect(labels[1]).toBe("age")
    expect(labels[2]).toBe("region_B")
    expect(labels[3]).toBe("region_C")
  })

  it("displays relativity values", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("0.291")).toBeTruthy()
    expect(screen.getByText("1.012")).toBeTruthy()
    expect(screen.getByText("1.169")).toBeTruthy()
    expect(screen.getByText("0.915")).toBeTruthy()
  })

  it("shows legend with baseline info and CI note when CIs present", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("Baseline = 1.0 (center line)")).toBeTruthy()
    expect(screen.getByText("— CI whiskers")).toBeTruthy()
    expect(screen.getByText("4 terms")).toBeTruthy()
  })

  it("hides CI note when no CI data", () => {
    const rows = makeGlmRelativities().map(r => ({ feature: r.feature, relativity: r.relativity }))
    const result = makeTrainResult({ glm_relativities: rows })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.queryByText("— CI whiskers")).toBeNull()
  })

  it("shows empty state when no relativities", () => {
    const result = makeTrainResult({ glm_relativities: [] })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("No relativity data available")).toBeTruthy()
  })
})

// ═════════════════════════════════════════════════════════════════
// SummaryTab — GLM fit statistics
// ═════════════════════════════════════════════════════════════════

describe("SummaryTab (GLM extensions)", () => {
  it("shows GLM fit statistics card when present", () => {
    const result = makeTrainResult({
      glm_fit_statistics: { aic: 5432.1, bic: 5478.9, deviance: 4200.3, null_deviance: 5100.0 },
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Fit Statistics")).toBeTruthy()
    expect(screen.getByText("aic")).toBeTruthy()
    expect(screen.getByText("5432.1000")).toBeTruthy()
    expect(screen.getByText("bic")).toBeTruthy()
    expect(screen.getByText("5478.9000")).toBeTruthy()
  })

  it("hides fit statistics when not present", () => {
    const result = makeTrainResult()
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.queryByText("Fit Statistics")).toBeNull()
  })

  it("shows regularization info when present", () => {
    const result = makeTrainResult({
      glm_regularization_path: { selected_alpha: 0.001234, n_nonzero: 12 },
    })
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    expect(screen.getByText("Regularization")).toBeTruthy()
    expect(screen.getByText("Alpha")).toBeTruthy()
    expect(screen.getByText("0.001234")).toBeTruthy()
    expect(screen.getByText("Non-zero")).toBeTruthy()
    expect(screen.getByText("12")).toBeTruthy()
  })

  it("hides regularization when no path info", () => {
    const result = makeTrainResult()
    render(<SummaryTab result={result} jobId="j1" mlflowBackend={null} config={{}} />)
    // "Regularization" appears as a header in GLMRegularizationConfig but not in SummaryTab
    expect(screen.queryByText("Alpha")).toBeNull()
    expect(screen.queryByText("Non-zero")).toBeNull()
  })
})

// ═════════════════════════════════════════════════════════════════
// ModellingConfig — GLM routing
// ═════════════════════════════════════════════════════════════════

describe("ModellingConfig (GLM routing)", () => {
  it("algorithm picker shows GLM option", () => {
    render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={{ _nodeId: "n1" }}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
        />
      </GraphProvider>,
    )
    expect(screen.getByText("GLM")).toBeTruthy()
    expect(screen.getByText(/Generalised linear model/)).toBeTruthy()
  })

  it("clicking GLM sets algorithm", () => {
    const onUpdate = vi.fn()
    render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={{ _nodeId: "n1" }}
          onUpdate={onUpdate}
          upstreamColumns={defaultColumns}
        />
      </GraphProvider>,
    )
    fireEvent.click(screen.getByText("GLM"))
    expect(onUpdate).toHaveBeenCalledWith("algorithm", "glm")
  })

  it("GLM config renders target, factors, regularization sections", () => {
    render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={{ _nodeId: "n1", algorithm: "glm", target: "claim_count", weight: "exposure", family: "poisson" }}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
        />
      </GraphProvider>,
    )
    // GLM-specific sections
    expect(screen.getByText("Target & Weight")).toBeTruthy()
    expect(screen.getByText("Family")).toBeTruthy()
    expect(screen.getByText("Factors")).toBeTruthy()
    expect(screen.getByText("Regularization")).toBeTruthy()

    // Should NOT have CatBoost-specific sections
    expect(screen.queryByRole("button", { name: "regression" })).toBeNull()
    expect(screen.queryByRole("button", { name: "classification" })).toBeNull()
  })

  it("GLM config still renders shared sections (split, train actions)", () => {
    render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={{ _nodeId: "n1", algorithm: "glm", target: "claim_count", weight: "exposure", family: "poisson" }}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
        />
      </GraphProvider>,
    )
    // Shared sections
    expect(screen.getByRole("button", { name: /Train Model/ })).toBeTruthy()
  })
})
