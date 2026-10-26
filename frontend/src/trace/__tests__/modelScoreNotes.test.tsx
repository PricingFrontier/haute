import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { ModelScoreDetailBlock } from "../ModelScoreDetail"
import type { ModelScoreNodeDetail } from "../../types/trace"

describe("model-score trace notes", () => {
  afterEach(cleanup)

  it("says when a row was priced as a full policy term", () => {
    const note = "Priced as a full policy term: this row has no 'exposure'."
    render(
      <ModelScoreDetailBlock
        detail={{
          detail_type: "model_score",
          prediction_column: "pred",
          prediction_value: 1.2,
          feature_columns: ["age"],
          feature_values: { age: 30 },
          explanation: { status: "ok", notes: [note] },
        } as ModelScoreNodeDetail}
      />,
    )

    expect(screen.getByTestId("model-score-note")).toHaveTextContent(note)
  })
})
