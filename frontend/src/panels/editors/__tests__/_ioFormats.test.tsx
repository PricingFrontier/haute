import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { IoCapabilitiesResponse } from "../../../api/types"

const mockFetchIoCapabilities = vi.fn()

vi.mock("../../../api/client", () => ({
  fetchIoCapabilities: (...args: unknown[]) => mockFetchIoCapabilities(...args),
}))

import { resetIoCapabilitiesRequestForTests, useIoCapabilities } from "../_ioFormats"

function capabilities(label: string): IoCapabilitiesResponse {
  return {
    schema_version: 1,
    groups: [
      {
        name: "file",
        label,
        input_available: true,
        output_available: true,
        cache_modes: ["snapshot"],
        input_fields: [],
        output_fields: [],
        formats: [],
      },
    ],
  }
}

function CapabilityProbe({ name }: { name: string }) {
  const { capabilities: result, error } = useIoCapabilities()
  return <div data-testid={name}>{error ?? result?.groups[0]?.label ?? "Loading"}</div>
}

describe("useIoCapabilities", () => {
  beforeEach(() => {
    mockFetchIoCapabilities.mockReset()
    resetIoCapabilitiesRequestForTests()
  })

  afterEach(cleanup)

  it("refetches capabilities for a later editor mount", async () => {
    mockFetchIoCapabilities
      .mockResolvedValueOnce(capabilities("First response"))
      .mockResolvedValueOnce(capabilities("Updated response"))

    const first = render(<CapabilityProbe name="first" />)
    expect(await screen.findByText("First response")).toBeInTheDocument()
    first.unmount()

    render(<CapabilityProbe name="second" />)
    expect(await screen.findByText("Updated response")).toBeInTheDocument()
    expect(mockFetchIoCapabilities).toHaveBeenCalledTimes(2)
  })

  it("coalesces capability requests for concurrent mounts", async () => {
    let resolveRequest!: (value: IoCapabilitiesResponse) => void
    mockFetchIoCapabilities.mockReturnValueOnce(
      new Promise<IoCapabilitiesResponse>((resolve) => {
        resolveRequest = resolve
      }),
    )

    render(
      <>
        <CapabilityProbe name="first" />
        <CapabilityProbe name="second" />
      </>,
    )

    expect(mockFetchIoCapabilities).toHaveBeenCalledTimes(1)
    resolveRequest(capabilities("Shared response"))

    await waitFor(() => {
      expect(screen.getAllByText("Shared response")).toHaveLength(2)
    })
  })
})
