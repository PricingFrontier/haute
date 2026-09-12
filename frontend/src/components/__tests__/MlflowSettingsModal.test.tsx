/**
 * MlflowSettingsModal — the MLflow destinations inventory editor.
 *
 * One `it` per behaviour bullet of the modal contract in
 * `specs/frontend-shared/low-level.md` ("MLflow settings modal"): the two
 * prefilled fields, the three read-only Databricks block variants, one test
 * action per remote with its own inline result and sequence guard, Save
 * PUTting both drafts then invalidating the inventory, a `400` detail with no
 * invalidation, and every input locked while the save is in flight.
 *
 * The API layer is mocked; the inventory is driven through the real settings
 * store (`setState`) with only the two actions the modal may call replaced by
 * spies, since their invocation is itself part of the contract.
 */
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>()
  return {
    ApiError: actual.ApiError,
    getMlflowDestinations: vi.fn(),
    getMlflowSettings: vi.fn(),
    putMlflowSettings: vi.fn(),
    testMlflowConnection: vi.fn(),
  }
})

import MlflowSettingsModal from "../MlflowSettingsModal"
import {
  ApiError,
  getMlflowSettings,
  putMlflowSettings,
  testMlflowConnection,
} from "../../api/client"
import useSettingsStore from "../../stores/useSettingsStore"
import type {
  MlflowDestinationEntry,
  MlflowDestinationKey,
  MlflowSettingsResponse,
  MlflowTestConnectionResponse,
} from "../../api/types"

type MlflowSlice = ReturnType<typeof useSettingsStore.getState>["mlflow"]

const SETTINGS: MlflowSettingsResponse = {
  section_present: true,
  tracking_uri: "http://localhost:5000",
  folder: "runs",
  resolved_folder: "C:/proj/runs",
  detail: "",
}

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

const DATABRICKS_PROFILE = entry("databricks", { destination: "databricks://team" })
const DATABRICKS_HOST = entry("databricks", { destination: "https://adb-42.azuredatabricks.net" })
const DATABRICKS_UNCONFIGURED = entry("databricks", {
  configured: false,
  config_source: "",
  detail: "Set MLFLOW_TRACKING_URI=databricks://<profile>, or DATABRICKS_HOST and DATABRICKS_TOKEN.",
})
const SERVER = entry("server", { destination: "http://localhost:5000", config_source: "toml" })
const LOCAL = entry("local", { destination: "C:/proj/runs", config_source: "toml" })

function setInventory(over: Partial<MlflowSlice> = {}): void {
  useSettingsStore.setState({
    mlflow: {
      status: "ready",
      installed: true,
      importable: true,
      auto: "databricks",
      destinations: [DATABRICKS_PROFILE, SERVER, LOCAL],
      detail: "",
      ...over,
    },
  })
}

let fetchMlflow: Mock<() => void>
let invalidateMlflow: Mock<() => void>

beforeEach(() => {
  vi.clearAllMocks()
  fetchMlflow = vi.fn<() => void>()
  invalidateMlflow = vi.fn<() => void>()
  useSettingsStore.setState({ fetchMlflow, invalidateMlflow })
  setInventory()
  vi.mocked(getMlflowSettings).mockResolvedValue(SETTINGS)
})

afterEach(cleanup)

async function renderModal(onClose = vi.fn()) {
  render(<MlflowSettingsModal onClose={onClose} />)
  await waitFor(() => {
    expect(screen.getByLabelText(/mlflow server url/i)).toBeInTheDocument()
  })
  return onClose
}

function serverField(): HTMLElement {
  return screen.getByLabelText(/mlflow server url/i)
}

function folderField(): HTMLElement {
  return screen.getByLabelText(/local folder/i)
}

function databricksBlock(): HTMLElement {
  return screen.getByTestId("mlflow-databricks-block")
}

describe("MlflowSettingsModal", () => {
  it("loads the settings and the inventory on mount", async () => {
    setInventory({ status: "pending", installed: null, importable: null, destinations: [] })
    await renderModal()
    expect(getMlflowSettings).toHaveBeenCalledTimes(1)
    expect(fetchMlflow).toHaveBeenCalled()
  })

  it("prefills both fields and names the resolved folder", async () => {
    await renderModal()
    expect(serverField()).toHaveValue("http://localhost:5000")
    expect(folderField()).toHaveValue("runs")
    expect(folderField()).toHaveAttribute("placeholder", "C:/proj/runs")
    expect(screen.getByText("Resolves to: C:/proj/runs")).toBeInTheDocument()
  })

  it("names the selected Databricks profile", async () => {
    await renderModal()
    expect(databricksBlock()).toHaveTextContent("Profile: team (from MLFLOW_TRACKING_URI)")
  })

  it("names the detected host when no Databricks profile is selected", async () => {
    setInventory({ destinations: [DATABRICKS_HOST, SERVER, LOCAL] })
    await renderModal()
    expect(databricksBlock()).toHaveTextContent(
      "Host: https://adb-42.azuredatabricks.net (from DATABRICKS_HOST)",
    )
  })

  it("names the missing configuration when Databricks is unconfigured", async () => {
    setInventory({ destinations: [DATABRICKS_UNCONFIGURED, SERVER, LOCAL] })
    await renderModal()
    expect(databricksBlock()).toHaveTextContent(DATABRICKS_UNCONFIGURED.detail)
  })

  it("tests the server against the drafted URL, in the server result area", async () => {
    vi.mocked(testMlflowConnection).mockResolvedValue({ ok: true, category: "", detail: "" })
    await renderModal()

    fireEvent.change(serverField(), { target: { value: " http://localhost:6000 " } })
    fireEvent.click(screen.getByRole("button", { name: /test server/i }))

    await waitFor(() => {
      expect(testMlflowConnection).toHaveBeenCalledWith({
        destination: "server",
        tracking_uri: "http://localhost:6000",
      })
    })
    await waitFor(() => {
      expect(within(screen.getByTestId("mlflow-test-result-server")).getByText(/connection ok/i))
        .toBeInTheDocument()
    })
    expect(screen.queryByTestId("mlflow-test-result-databricks")).toBeNull()
  })

  it("tests Databricks with the destination key alone, in its own result area", async () => {
    vi.mocked(testMlflowConnection).mockResolvedValue({
      ok: false,
      category: "authentication",
      detail: "Databricks rejected the credentials.",
    })
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /test databricks/i }))

    await waitFor(() => {
      expect(testMlflowConnection).toHaveBeenCalledWith({ destination: "databricks" })
    })
    await waitFor(() => {
      expect(
        within(screen.getByTestId("mlflow-test-result-databricks"))
          .getByText(/Databricks rejected the credentials/),
      ).toBeInTheDocument()
    })
    expect(screen.queryByTestId("mlflow-test-result-server")).toBeNull()
  })

  it("clears a displayed test result when a draft is edited", async () => {
    vi.mocked(testMlflowConnection).mockResolvedValue({ ok: true, category: "", detail: "" })
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /test server/i }))
    await waitFor(() => {
      expect(screen.getByTestId("mlflow-test-result-server")).toBeInTheDocument()
    })

    fireEvent.change(folderField(), { target: { value: "other" } })
    expect(screen.queryByTestId("mlflow-test-result-server")).toBeNull()
  })

  it("discards a stale test completion that lands after an edit", async () => {
    let resolveProbe: (value: MlflowTestConnectionResponse) => void = () => {}
    vi.mocked(testMlflowConnection).mockReturnValue(
      new Promise<MlflowTestConnectionResponse>((resolve) => {
        resolveProbe = resolve
      }),
    )
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /test server/i }))
    fireEvent.change(serverField(), { target: { value: "http://localhost:7000" } })
    resolveProbe({ ok: true, category: "", detail: "" })

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /test server/i })).toBeEnabled()
    })
    expect(screen.queryByTestId("mlflow-test-result-server")).toBeNull()
  })

  it("saves both trimmed drafts and then invalidates the inventory", async () => {
    const updated: MlflowSettingsResponse = {
      ...SETTINGS,
      tracking_uri: "http://localhost:6000",
      folder: "runs2",
      resolved_folder: "C:/proj/runs2",
    }
    vi.mocked(putMlflowSettings).mockResolvedValue(updated)
    await renderModal()

    fireEvent.change(serverField(), { target: { value: "  http://localhost:6000  " } })
    fireEvent.change(folderField(), { target: { value: "  runs2  " } })
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      expect(putMlflowSettings).toHaveBeenCalledWith({
        tracking_uri: "http://localhost:6000",
        folder: "runs2",
      })
    })
    expect(invalidateMlflow).toHaveBeenCalledTimes(1)
    await waitFor(() => {
      expect(screen.getByText("Resolves to: C:/proj/runs2")).toBeInTheDocument()
    })
  })

  it("surfaces a 400 detail and does not invalidate the inventory", async () => {
    vi.mocked(putMlflowSettings).mockRejectedValue(
      new ApiError("Bad Request", 400, "[mlflow] tracking_uri must be an http(s):// URL."),
    )
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/\[mlflow\] tracking_uri must be an http\(s\):\/\/ URL\./),
      ).toBeInTheDocument()
    })
    expect(invalidateMlflow).not.toHaveBeenCalled()
  })

  it("locks every input while a save is in flight", async () => {
    let resolvePut: (value: MlflowSettingsResponse) => void = () => {}
    vi.mocked(putMlflowSettings).mockReturnValue(
      new Promise<MlflowSettingsResponse>((resolve) => {
        resolvePut = resolve
      }),
    )
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    expect(serverField()).toBeDisabled()
    expect(folderField()).toBeDisabled()
    expect(screen.getByRole("button", { name: /test server/i })).toBeDisabled()
    expect(screen.getByRole("button", { name: /test databricks/i })).toBeDisabled()

    resolvePut(SETTINGS)
    await waitFor(() => {
      expect(serverField()).toBeEnabled()
    })
    expect(screen.getByText(/saved/i)).toBeInTheDocument()
  })

  it("shows a failed settings load instead of editable empty drafts", async () => {
    vi.mocked(getMlflowSettings).mockRejectedValue(new Error("Settings unavailable"))
    render(<MlflowSettingsModal onClose={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/settings unavailable/i)).toBeInTheDocument()
    })
    expect(screen.queryByLabelText(/mlflow server url/i)).toBeNull()
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull()
  })

  it("close calls onClose", async () => {
    const onClose = await renderModal()
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
