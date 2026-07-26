import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { BandingDetailBlock } from "../BandingDetail"
import { ModelScoreDetailBlock } from "../ModelScoreDetail"
import type { BandingNodeDetail, ModelScoreNodeDetail } from "../../types/trace"

describe("trace detail root errors", () => {
  afterEach(cleanup)

  it("renders a banding root error without its normal detail summary or rows", () => {
    render(
      <BandingDetailBlock
        detail={{
          detail_type: "banding",
          output_column: "band",
          input_column: "age",
          input_value: null,
          matched_band: null,
          error: "Banding evaluation failed",
          error_type: "ValueError",
        } as unknown as BandingNodeDetail}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      "ValueError: Banding evaluation failed",
    )
    expect(screen.queryByText("band")).not.toBeInTheDocument()
  })

  it("renders a model-score root error without predictions or feature rows", () => {
    render(
      <ModelScoreDetailBlock
        detail={{
          detail_type: "model_score",
          prediction_column: "prediction",
          prediction_value: null,
          feature_columns: ["age"],
          feature_values: { age: null },
          error: "Model scoring failed",
          error_type: "RuntimeError",
        } as unknown as ModelScoreNodeDetail}
      />,
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      "RuntimeError: Model scoring failed",
    )
    expect(screen.queryByText(/Prediction:/)).not.toBeInTheDocument()
    expect(screen.queryByText("age")).not.toBeInTheDocument()
  })
})
