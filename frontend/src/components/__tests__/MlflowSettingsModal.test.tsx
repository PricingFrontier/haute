/**
 * MlflowSettingsModal — settings GET rendering, mode cards, server URL
 * round trip, save→PUT→invalidateMlflow, 400 surfacing, test-connection
 * results. Per specs/frontend-shared/low-level.md (MLflow settings surface).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>()
  return {
    ApiError: actual.ApiError,
    getMlflowSettings: vi.fn(),
    putMlflowSettings: vi.fn(),
    testMlflowConnection: vi.fn(),
  }
})

const mockInvalidateMlflow = vi.hoisted(() => vi.fn())
vi.mock("../../stores/useSettingsStore", () => {
  const state = { invalidateMlflow: mockInvalidateMlflow }
  const hook = (selector?: (s: typeof state) => unknown) => (selector ? selector(state) : state)
  return { default: hook }
})

import MlflowSettingsModal from "../MlflowSettingsModal"
import { getMlflowSettings, putMlflowSettings, testMlflowConnection, ApiError } from "../../api/client"
import type { MlflowSettingsResponse } from "../../api/types"

const SERVER_SETTINGS: MlflowSettingsResponse = {
  section_present: true,
  mode: "server",
  tracking_uri: "http://localhost:5000",
  folder: "",
  resolved: {
    mode: "server",
    destination: "http://localhost:5000",
    config_source: "toml",
  },
  detail: "",
}

const DEFAULT_LOCAL_SETTINGS: MlflowSettingsResponse = {
  section_present: false,
  mode: "",
  tracking_uri: "",
  folder: "",
  resolved: {
    mode: "local",
    destination: "C:/proj/mlruns",
    config_source: "default",
  },
  detail: "",
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getMlflowSettings).mockResolvedValue(SERVER_SETTINGS)
})

afterEach(cleanup)

async function renderModal(onClose = vi.fn()) {
  render(<MlflowSettingsModal onClose={onClose} />)
  await waitFor(() => {
    expect(screen.getByRole("radiogroup", { name: /tracking destination/i })).toBeInTheDocument()
  })
  return onClose
}

describe("MlflowSettingsModal", () => {
  it("renders the current resolution and three mode cards from the settings GET", async () => {
    await renderModal()
    expect(screen.getByText(/MLflow server — http:\/\/localhost:5000/)).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /local folder/i })).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /mlflow server/i })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getByRole("radio", { name: /databricks/i })).toBeInTheDocument()
  })

  it("shows the resolved folder on the local card", async () => {
    vi.mocked(getMlflowSettings).mockResolvedValue(DEFAULT_LOCAL_SETTINGS)
    await renderModal()
    expect(screen.getByRole("radio", { name: /local folder/i })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    expect(screen.getAllByText(/C:\/proj\/mlruns/).length).toBeGreaterThan(0)
  })

  it("prefills the server URL field from the stored tracking_uri", async () => {
    await renderModal()
    expect(screen.getByLabelText(/server url/i)).toHaveValue("http://localhost:5000")
  })

  it("saves through PUT with an empty folder and then invalidates the status cache", async () => {
    vi.mocked(putMlflowSettings).mockResolvedValue({
      ...SERVER_SETTINGS,
      tracking_uri: "http://localhost:6000",
      resolved: {
        mode: "server",
        destination: "http://localhost:6000",
        config_source: "toml",
      },
    })
    await renderModal()

    const field = screen.getByLabelText(/server url/i)
    fireEvent.change(field, { target: { value: "http://localhost:6000" } })
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      expect(putMlflowSettings).toHaveBeenCalledWith({
        mode: "server",
        tracking_uri: "http://localhost:6000",
        folder: "",
      })
    })
    expect(mockInvalidateMlflow).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/MLflow server — http:\/\/localhost:6000/)).toBeInTheDocument()
  })

  it("switching to local saves mode local without a tracking uri", async () => {
    vi.mocked(putMlflowSettings).mockResolvedValue(DEFAULT_LOCAL_SETTINGS)
    await renderModal()

    fireEvent.click(screen.getByRole("radio", { name: /local folder/i }))
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      expect(putMlflowSettings).toHaveBeenCalledWith({
        mode: "local",
        tracking_uri: "",
        folder: "",
      })
    })
  })

  it("surfaces a 400 detail in the error area and does not invalidate", async () => {
    vi.mocked(putMlflowSettings).mockRejectedValue(
      new ApiError(
        "Bad Request",
        400,
        "[mlflow] server mode requires an http(s):// tracking_uri.",
      ),
    )
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /^save$/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/server mode requires an http\(s\):\/\/ tracking_uri/),
      ).toBeInTheDocument()
    })
    expect(mockInvalidateMlflow).not.toHaveBeenCalled()
  })

  it("renders a successful connection test inline", async () => {
    vi.mocked(testMlflowConnection).mockResolvedValue({ ok: true, category: "", detail: "" })
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }))

    await waitFor(() => {
      expect(screen.getByText(/connection ok/i)).toBeInTheDocument()
    })
  })

  it("renders a categorised connection failure inline", async () => {
    vi.mocked(testMlflowConnection).mockResolvedValue({
      ok: false,
      category: "connectivity",
      detail: "Connection test failed (connectivity).",
    })
    await renderModal()

    fireEvent.click(screen.getByRole("button", { name: /test connection/i }))

    await waitFor(() => {
      expect(screen.getByText(/Connection test failed \(connectivity\)/)).toBeInTheDocument()
    })
  })

  it("close button calls onClose", async () => {
    const onClose = await renderModal()
    fireEvent.click(screen.getByRole("button", { name: /^close$/i }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
