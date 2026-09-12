/**
 * MlflowDestinationSelector — the shared per-node MLflow destination control.
 *
 * One `it` per behaviour bullet of the component contract in
 * `specs/frontend-shared/low-level.md` ("MLflow destination selector"). The
 * inventory is driven through the real settings store (`setState`) and the
 * modal flag through the real UI store; only the two store actions the
 * component is allowed to call (`fetchMlflow`, `invalidateMlflow`) are
 * replaced with spies, since their invocation is itself part of the contract.
 */
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"

import MlflowDestinationSelector from "../MlflowDestinationSelector"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import type { MlflowDestinationEntry, MlflowDestinationKey } from "../../api/types"

type MlflowSlice = ReturnType<typeof useSettingsStore.getState>["mlflow"]

function entry(
  key: MlflowDestinationKey,
  over: Partial<MlflowDestinationEntry> = {},
): MlflowDestinationEntry {
  return {
    key,
    configured: true,
    destination: "",
    config_source: "env",
    detail: "",
    probed: false,
    ok: false,
    category: "",
    ...over,
  }
}

const DATABRICKS_GREEN = entry("databricks", {
  destination: "databricks://team",
  probed: true,
  ok: true,
})
const DATABRICKS_GREY = entry("databricks", {
  configured: false,
  config_source: "",
  detail: "Set MLFLOW_TRACKING_URI=databricks://<profile>.",
})
const SERVER_AMBER = entry("server", {
  destination: "http://mlflow.example:5000",
  config_source: "toml",
  probed: true,
  ok: false,
  detail: "Connection refused.",
  category: "connectivity",
})
const SERVER_GREY = entry("server", {
  configured: false,
  config_source: "",
  detail: "Set [mlflow] tracking_uri in haute.toml.",
})
const LOCAL = entry("local", {
  destination: "C:/proj/mlruns",
  config_source: "default",
})

function setInventory(over: Partial<MlflowSlice> = {}): void {
  useSettingsStore.setState({
    mlflow: {
      status: "ready",
      installed: true,
      importable: true,
      auto: "databricks",
      destinations: [DATABRICKS_GREEN, SERVER_AMBER, LOCAL],
      detail: "",
      ...over,
    },
  })
}

function renderSelector(
  props: Partial<React.ComponentProps<typeof MlflowDestinationSelector>> = {},
) {
  const onChange = vi.fn()
  const merged = { value: "", onChange, ...props }
  render(<MlflowDestinationSelector {...merged} />)
  return { onChange: merged.onChange }
}

function radio(name: string): HTMLElement {
  return screen.getByRole("radio", { name })
}

function tooltipOf(key: MlflowDestinationKey): string {
  const option = screen.getByTestId(`mlflow-option-${key}`)
  fireEvent.mouseEnter(option)
  return within(option).getByRole("tooltip", { hidden: true }).textContent ?? ""
}

let fetchMlflow: Mock<() => void>
let invalidateMlflow: Mock<() => void>

beforeEach(() => {
  fetchMlflow = vi.fn<() => void>()
  invalidateMlflow = vi.fn<() => void>()
  useSettingsStore.setState({ fetchMlflow, invalidateMlflow })
  useUIStore.setState({ mlflowSettingsOpen: false })
  setInventory()
})

afterEach(cleanup)

describe("MlflowDestinationSelector", () => {
  it("renders a labelled radiogroup of the three destinations in fixed order", () => {
    renderSelector()

    expect(screen.getByRole("radiogroup", { name: "MLflow destination" })).toBeInTheDocument()
    const radios = screen.getAllByRole("radio")
    expect(radios).toHaveLength(3)
    expect(radios[0]).toHaveAccessibleName("Databricks")
    expect(radios[1]).toHaveAccessibleName("MLflow server")
    expect(radios[2]).toHaveAccessibleName("Local folder")
    radios.forEach((el) => expect(el).toHaveClass("sr-only"))
  })

  it("selects the effective destination, so the same node value follows auto", () => {
    renderSelector({ value: "" })
    expect(radio("Databricks")).toBeChecked()
    expect(radio("Local folder")).not.toBeChecked()

    cleanup()
    setInventory({ auto: "local" })
    renderSelector({ value: "" })
    expect(radio("Local folder")).toBeChecked()

    cleanup()
    renderSelector({ value: "server" })
    expect(radio("MLflow server")).toBeChecked()
    expect(radio("Local folder")).not.toBeChecked()
  })

  it("marks the selected option 'auto' and offers no 'Use auto' when the node stores nothing", () => {
    renderSelector({ value: "" })

    const suffix = screen.getByTestId("mlflow-destination-auto-suffix")
    expect(suffix).toHaveTextContent("auto")
    expect(within(screen.getByTestId("mlflow-option-databricks")).getByTestId(
      "mlflow-destination-auto-suffix",
    )).toBe(suffix)
    expect(screen.queryByRole("button", { name: "Use auto" })).toBeNull()
  })

  it("renders 'Use auto' which clears the stored choice when the node stores one", () => {
    const { onChange } = renderSelector({ value: "local" })

    expect(screen.queryByTestId("mlflow-destination-auto-suffix")).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Use auto" }))
    expect(onChange).toHaveBeenCalledWith("")
  })

  it("calls onChange with the key of a configured option that is clicked", () => {
    const { onChange } = renderSelector({ value: "" })

    fireEvent.click(radio("MLflow server"))
    expect(onChange).toHaveBeenCalledWith("server")

    fireEvent.click(radio("Local folder"))
    expect(onChange).toHaveBeenCalledWith("local")
  })

  it("greys an unconfigured remote: aria-disabled, no onChange, opens the settings modal", () => {
    setInventory({ auto: "local", destinations: [DATABRICKS_GREY, SERVER_AMBER, LOCAL] })
    const { onChange } = renderSelector({ value: "" })

    const databricks = radio("Databricks")
    expect(databricks).toHaveAttribute("aria-disabled", "true")

    fireEvent.click(databricks)
    expect(onChange).not.toHaveBeenCalled()
    expect(databricks).not.toBeChecked()
    expect(useUIStore.getState().mlflowSettingsOpen).toBe(true)
  })

  it("shows a light dot for each remote and none for local", () => {
    setInventory({ destinations: [DATABRICKS_GREEN, SERVER_AMBER, LOCAL] })
    renderSelector({ value: "" })

    expect(screen.getByTestId("mlflow-light-databricks")).toHaveAttribute("data-light", "green")
    expect(screen.getByTestId("mlflow-light-server")).toHaveAttribute("data-light", "amber")
    expect(screen.queryByTestId("mlflow-light-local")).toBeNull()

    cleanup()
    setInventory({ auto: "local", destinations: [DATABRICKS_GREY, SERVER_GREY, LOCAL] })
    renderSelector({ value: "" })
    expect(screen.getByTestId("mlflow-light-databricks")).toHaveAttribute("data-light", "grey")
    expect(screen.getByTestId("mlflow-light-server")).toHaveAttribute("data-light", "grey")

    cleanup()
    setInventory({ status: "pending", auto: "", destinations: [] })
    renderSelector({ value: "" })
    expect(screen.getByTestId("mlflow-light-databricks")).toHaveAttribute("data-light", "pending")
    expect(screen.getByTestId("mlflow-light-server")).toHaveAttribute("data-light", "pending")
    expect(screen.queryByTestId("mlflow-light-local")).toBeNull()
  })

  describe("remote tooltips name the light's meaning", () => {
    it("green names the resolved destination", () => {
      renderSelector({ value: "" })
      expect(tooltipOf("databricks")).toBe("databricks://team")
    })

    it("amber names the probe detail", () => {
      renderSelector({ value: "" })
      expect(tooltipOf("server")).toBe("Connection refused.")
    })

    it("grey names what to configure and that clicking configures it", () => {
      setInventory({ auto: "local", destinations: [DATABRICKS_GREY, SERVER_AMBER, LOCAL] })
      renderSelector({ value: "" })
      expect(tooltipOf("databricks")).toBe(
        "Not configured: Set MLFLOW_TRACKING_URI=databricks://<profile>. Click to configure.",
      )
    })

    it("pending says the connection is being checked", () => {
      setInventory({ status: "pending", auto: "", destinations: [] })
      renderSelector({ value: "" })
      expect(tooltipOf("databricks")).toBe("Checking connection…")
      expect(tooltipOf("server")).toBe("Checking connection…")
    })
  })

  describe("resolved destination line", () => {
    it("names the effective entry's label and destination", () => {
      renderSelector({ value: "" })
      expect(screen.getByTestId("mlflow-destination-resolved")).toHaveTextContent(
        "Databricks — databricks://team",
      )

      cleanup()
      renderSelector({ value: "local" })
      expect(screen.getByTestId("mlflow-destination-resolved")).toHaveTextContent(
        "Local folder — C:/proj/mlruns",
      )
    })

    it("names the entry's own detail when the effective destination is unconfigured", () => {
      setInventory({ auto: "local", destinations: [DATABRICKS_GREY, SERVER_GREY, LOCAL] })
      renderSelector({ value: "server" })

      expect(screen.getByTestId("mlflow-destination-resolved")).toHaveTextContent(
        "Set [mlflow] tracking_uri in haute.toml.",
      )
    })

    it("names the store's detail when the MLflow package is missing", () => {
      setInventory({
        status: "error",
        installed: false,
        importable: false,
        auto: "",
        destinations: [],
        detail: "MLflow is not installed.",
      })
      renderSelector({ value: "" })

      expect(screen.getByTestId("mlflow-destination-resolved")).toHaveTextContent(
        "MLflow is not installed.",
      )
    })
  })

  it("opens the settings modal from the gear button", () => {
    renderSelector({ value: "" })

    fireEvent.click(screen.getByRole("button", { name: "MLflow settings" }))
    expect(useUIStore.getState().mlflowSettingsOpen).toBe(true)
  })

  it("re-checks the inventory, and cannot while one is already in flight", () => {
    renderSelector({ value: "" })

    fireEvent.click(screen.getByRole("button", { name: "Re-check MLflow connections" }))
    expect(invalidateMlflow).toHaveBeenCalledTimes(1)

    cleanup()
    setInventory({ status: "pending", auto: "", destinations: [] })
    renderSelector({ value: "" })
    expect(screen.getByRole("button", { name: "Re-check MLflow connections" })).toBeDisabled()
  })

  it("disables the radios and says so while the inventory is loading", () => {
    setInventory({ status: "pending", auto: "", destinations: [] })
    renderSelector({ value: "" })

    screen.getAllByRole("radio").forEach((el) => expect(el).toBeDisabled())
    expect(screen.getByTestId("mlflow-destination-resolved")).toHaveTextContent("Checking MLflow…")
  })

  it("disables the radios and the clear control when the caller disables the control", () => {
    renderSelector({ value: "local", disabled: true })

    screen.getAllByRole("radio").forEach((el) => expect(el).toBeDisabled())
    expect(screen.getByRole("button", { name: "Use auto" })).toBeDisabled()
  })

  it("fetches the inventory on mount only while the store is pending", () => {
    setInventory({ status: "pending", auto: "", destinations: [] })
    renderSelector({ value: "" })
    expect(fetchMlflow).toHaveBeenCalledTimes(1)

    cleanup()
    fetchMlflow.mockClear()
    setInventory()
    renderSelector({ value: "" })
    expect(fetchMlflow).not.toHaveBeenCalled()
  })

  it("names its radio group from idPrefix so two selectors stay independent", () => {
    renderSelector({ value: "" })
    expect(radio("Databricks")).toHaveAttribute("name", "mlflow-destination")

    cleanup()
    renderSelector({ value: "", idPrefix: "model-score-destination" })
    expect(radio("Databricks")).toHaveAttribute("name", "model-score-destination")
  })
})
