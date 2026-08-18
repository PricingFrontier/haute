import { render } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { ExploreCacheReport } from "../../../api/types"
import useNodeResultsStore, {
  explorePivotResultKey,
  resetNodeResultsDerivedCaches,
} from "../../../stores/useNodeResultsStore"
import {
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "../pivotConfig"
import useAutoUpdateExplorePivots from "../useAutoUpdateExplorePivots"

const NODE_ID = "explore_1"

function pivot(): ExplorePivotConfig {
  return {
    version: 1,
    id: "claims",
    name: "Claims",
    enabled: true,
    filters: [],
    rows: [{ id: "region", field: "region", number_format: "general", decimal_places: null, use_grouping: true }],
    columns: [],
    values: [
      { id: "paid", field: "paid", aggregation: "sum", reference: "paid_sum", display_name: "Paid", color_scale_split_by: null, number_format: "general", decimal_places: null, use_grouping: true },
    ],
    formulas: [],
    value_order: ["paid"],
    options: { row_grand_totals: true, column_grand_totals: true },
  }
}

function formulaOnlyPivot(): ExplorePivotConfig {
  return {
    ...pivot(),
    values: [],
    formulas: [{
      id: "formula_1",
      reference: "claim_count",
      display_name: "Claim count",
      expression: "pl.len()",
      number_format: "general",
      decimal_places: null,
      use_grouping: true,
    }],
    value_order: ["formula_1"],
  }
}

const report = { dataframe_cache_key: "df-1" } as ExploreCacheReport

function Consumer({
  updatePivot,
  activeReport = report,
  submitting = {},
  pivots = [pivot()],
}: {
  updatePivot: (
    target: ExplorePivotConfig,
    requestedDataframeCacheKey?: string | null,
    autoClaimToken?: number,
  ) => Promise<void>
  activeReport?: ExploreCacheReport
  submitting?: Readonly<Record<string, boolean>>
  pivots?: readonly ExplorePivotConfig[]
}) {
  useAutoUpdateExplorePivots({
    nodeId: NODE_ID,
    pivots,
    report: activeReport,
    submitting,
    updatePivot,
  })
  return null
}

describe("useAutoUpdateExplorePivots claim serialisation", () => {
  beforeEach(() => {
    resetNodeResultsDerivedCaches()
    useNodeResultsStore.setState({
      pivotResults: {},
      pivotJobs: {},
      pivotStartClaims: {},
    })
  })

  it("admits exactly one submission for a single mounted consumer", () => {
    const updatePivot = vi.fn().mockResolvedValue(undefined)
    render(<Consumer updatePivot={updatePivot} />)

    expect(updatePivot).toHaveBeenCalledTimes(1)
    expect(updatePivot).toHaveBeenCalledWith(
      expect.objectContaining({ id: "claims" }),
      "df-1",
      expect.any(Number),
    )
  })

  it("schedules a pivot configured with a selected formula and no ordinary Values", () => {
    const updatePivot = vi.fn().mockResolvedValue(undefined)

    render(<Consumer updatePivot={updatePivot} pivots={[formulaOnlyPivot()]} />)

    expect(updatePivot).toHaveBeenCalledTimes(1)
    expect(updatePivot).toHaveBeenCalledWith(
      expect.objectContaining({ id: "claims", values: [], value_order: ["formula_1"] }),
      "df-1",
      expect.any(Number),
    )
  })

  it("admits exactly one submission across simultaneously mounted consumers", () => {
    const updatePivot = vi.fn().mockResolvedValue(undefined)
    render(
      <>
        <Consumer updatePivot={updatePivot} />
        <Consumer updatePivot={updatePivot} />
      </>,
    )

    expect(updatePivot).toHaveBeenCalledTimes(1)
    const key = explorePivotResultKey(NODE_ID, "claims")
    const claim = useNodeResultsStore.getState().pivotStartClaims[key]
    expect(claim).toMatchObject({
      dataframeCacheKey: "df-1",
      calculationIdentity: pivotCalculationIdentity(pivot()),
    })
    expect(updatePivot.mock.calls[0][2]).toBe(claim.token)
  })

  it("supersedes its own pending target when a newer report arrives mid-flight", () => {
    const updatePivot = vi.fn().mockResolvedValue(undefined)
    const view = render(<Consumer updatePivot={updatePivot} />)
    expect(updatePivot).toHaveBeenCalledTimes(1)
    const key = explorePivotResultKey(NODE_ID, "claims")
    const firstToken = updatePivot.mock.calls[0][2] as number

    // The first submission is still in flight (submitting true, claim held)
    // when a newer dataframe arrives: the newer target must replace the
    // claim and submit immediately rather than waiting behind the old one.
    const newerReport = { dataframe_cache_key: "df-2" } as ExploreCacheReport
    view.rerender(
      <Consumer
        updatePivot={updatePivot}
        activeReport={newerReport}
        submitting={{ claims: true }}
      />,
    )

    expect(updatePivot).toHaveBeenCalledTimes(2)
    expect(updatePivot.mock.calls[1][1]).toBe("df-2")
    const claim = useNodeResultsStore.getState().pivotStartClaims[key]
    expect(claim.dataframeCacheKey).toBe("df-2")
    expect(claim.token).not.toBe(firstToken)
    expect(updatePivot.mock.calls[1][2]).toBe(claim.token)
  })
})
