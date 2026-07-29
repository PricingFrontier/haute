import {
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react"
import { useState } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { OnUpdateConfig } from "../../editors"
import { HyperparametersConfig } from "../HyperparametersConfig"
import { formatHyperparameters } from "../hyperparameters"

const DEFAULTS = {
  iterations: 1000,
  learning_rate: 0.05,
  depth: 6,
}

function Harness({
  params = {},
  onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true })),
}: {
  params?: Record<string, unknown>
  onUpdate?: OnUpdateConfig
}) {
  const [draft, setDraft] = useState(
    formatHyperparameters(params, DEFAULTS, ["task_type"]),
  )
  return (
    <HyperparametersConfig
      algorithmLabel="CatBoost"
      params={params}
      defaultParams={DEFAULTS}
      reservedKeys={["task_type"]}
      reservedKeysHelp="GPU training is configured in the Train pane."
      onUpdate={onUpdate}
      draft={draft}
      setDraft={setDraft}
    />
  )
}

describe("HyperparametersConfig", () => {
  afterEach(cleanup)

  it("renders one styled JSON editor seeded with defaults and no parameter boxes", () => {
    render(<Harness />)

    expect(screen.getByText("Hyperparameters")).toBeInTheDocument()
    expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue(
      JSON.stringify(DEFAULTS, null, 2),
    )
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0)
    expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveClass(
      "font-mono",
    )
    expect(
      screen.queryByText(
        "Any valid CatBoost parameter as a JSON object. GPU training is configured in the Train pane.",
      ),
    ).not.toBeInTheDocument()
  })

  it("round-trips arbitrary current and future parameter shapes", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(
      <Harness
        params={{ stale: true, task_type: "GPU" }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), {
      target: {
        value: JSON.stringify({
          grow_policy: "Lossguide",
          max_leaves: 64,
          custom_nested: { enabled: true, choices: [1, "two"] },
        }),
      },
    })
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))

    expect(onUpdate).toHaveBeenCalledWith("params", {
      grow_policy: "Lossguide",
      max_leaves: 64,
      custom_nested: { enabled: true, choices: [1, "two"] },
      task_type: "GPU",
    })
  })

  it.each(["null", "[]", '"scalar"', "{invalid"])(
    "keeps an invalid or non-object draft local: %s",
    (draft) => {
      const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
      render(<Harness onUpdate={onUpdate} />)

      fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), {
        target: { value: draft },
      })
      fireEvent.click(screen.getByRole("button", { name: "Apply" }))

      expect(screen.getByRole("alert")).toBeInTheDocument()
      expect(onUpdate).not.toHaveBeenCalled()
      cleanup()
    },
  )

  it("rejects a reserved key explicitly instead of silently overriding it", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(<Harness onUpdate={onUpdate} />)

    fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), {
      target: { value: '{"task_type":"CPU"}' },
    })
    fireEvent.click(screen.getByRole("button", { name: "Apply" }))

    expect(screen.getByRole("alert")).toHaveTextContent(
      "task_type is managed elsewhere",
    )
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("reverts a dirty draft to the stored editable projection", () => {
    render(<Harness params={{ depth: 8, custom: true, task_type: "GPU" }} />)
    const editor = screen.getByLabelText("CatBoost hyperparameters JSON")

    fireEvent.change(editor, { target: { value: "{dirty" } })
    fireEvent.click(screen.getByRole("button", { name: "Revert" }))

    expect(editor).toHaveValue(
      JSON.stringify({ depth: 8, custom: true }, null, 2),
    )
  })
})
