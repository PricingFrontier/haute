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
import {
  formatHyperparameters,
  formatTuningSearchSpace,
} from "../hyperparameters"

const STARTER_SEARCH_SPACE = {
  depth: [4, 6, 8, 10],
  learning_rate: [0.01, 0.03, 0.05, 0.1, 0.2],
  l2_leaf_reg: [1, 3, 5, 10],
}
const FORMATTED_STARTER_SEARCH_SPACE = [
  "{",
  '  "depth": [4, 6, 8, 10],',
  '  "learning_rate": [0.01, 0.03, 0.05, 0.1, 0.2],',
  '  "l2_leaf_reg": [1, 3, 5, 10]',
  "}",
].join("\n")

function Harness({
  params = {},
  tuning = null,
  evaluation = {
    schema_version: 1,
    strategy: "random",
    seed: 42,
    validation: { method: "cross_validation", fold_count: 5 },
  },
  onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true })),
}: {
  params?: Record<string, unknown>
  tuning?: Record<string, unknown> | null
  evaluation?: Record<string, unknown>
  onUpdate?: OnUpdateConfig
}) {
  const [draft, setDraft] = useState(
    formatHyperparameters(params, ["task_type"]),
  )
  const [searchSpaceDraft, setSearchSpaceDraft] = useState(
    formatTuningSearchSpace(
      (tuning?.search_space as Record<string, unknown> | undefined) ?? {},
    ),
  )
  return (
    <HyperparametersConfig
      algorithmLabel="CatBoost"
      params={params}
      reservedKeys={["task_type"]}
      reservedKeysHelp="GPU training is configured in the Train pane."
      onUpdate={onUpdate}
      draft={draft}
      setDraft={setDraft}
      tuning={tuning}
      evaluation={evaluation}
      metrics={["gini", "rmse"]}
      searchSpaceDraft={searchSpaceDraft}
      setSearchSpaceDraft={setSearchSpaceDraft}
    />
  )
}

describe("HyperparametersConfig", () => {
  afterEach(cleanup)

  it("keeps choice lists inline while indenting conditional entries", () => {
    expect(formatTuningSearchSpace({
      grow_policy: ["SymmetricTree", "Depthwise"],
      min_data_in_leaf: {
        choices: [10, 25, 50],
        when: { grow_policy: ["Depthwise"] },
      },
    })).toBe([
      "{",
      '  "grow_policy": ["SymmetricTree", "Depthwise"],',
      '  "min_data_in_leaf": {',
      '    "choices": [10, 25, 50],',
      '    "when": {',
      '      "grow_policy": ["Depthwise"]',
      "    }",
      "  }",
      "}",
    ].join("\n"))
  })

  it("puts the strategy first and shows a single visible fixed-parameter JSON editor", () => {
    render(<Harness />)

    const strategy = screen.getByRole("radiogroup", {
      name: "Parameter strategy",
    })
    const editor = screen.getByLabelText("CatBoost hyperparameters JSON")
    expect(
      strategy.compareDocumentPosition(editor)
      & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(editor).toHaveValue(
      "{}",
    )
    expect(editor).toBeVisible()
    expect(screen.getAllByRole("textbox")).toHaveLength(1)
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument()
    expect(screen.queryByText("Advanced parameters")).not.toBeInTheDocument()
    expect(editor).toHaveClass("font-mono")
    expect(screen.queryByLabelText("CatBoost search space JSON")).toBeNull()
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Revert" })).toBeNull()
  })

  it("formats fixed JSON on blur while preserving parameters and GPU", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(<Harness params={{ task_type: "GPU", grow_policy: "Lossguide", depth: 6 }} onUpdate={onUpdate} />)
    const editor = screen.getByLabelText("CatBoost hyperparameters JSON")
    fireEvent.change(editor, { target: { value: '{"grow_policy":"Lossguide","depth":8}' } })
    fireEvent.blur(editor)
    expect(onUpdate).toHaveBeenCalledWith("params", { task_type: "GPU", grow_policy: "Lossguide", depth: 8 })
    expect(editor).toHaveValue(formatHyperparameters({ grow_policy: "Lossguide", depth: 8 }))
  })

  it("autosaves arbitrary current and future fixed-parameter shapes", () => {
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

      expect(screen.getByLabelText("CatBoost hyperparameters JSON")).toHaveValue(
        draft,
      )
      expect(screen.getByRole("alert")).toBeInTheDocument()
      expect(onUpdate).not.toHaveBeenCalled()
    },
  )

  it("keeps a reserved fixed key local instead of silently overriding it", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(<Harness onUpdate={onUpdate} />)

    fireEvent.change(screen.getByLabelText("CatBoost hyperparameters JSON"), {
      target: { value: '{"task_type":"CPU"}' },
    })
    expect(screen.getByRole("alert")).toBeInTheDocument()
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("uses a radio group and selecting Tune parameters seeds tuning without changing the evaluation split", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(<Harness onUpdate={onUpdate} />)

    expect(screen.getByRole("radio", { name: "Fixed parameters" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    fireEvent.click(screen.getByRole("radio", { name: "Tune parameters" }))

    expect(onUpdate).toHaveBeenCalledWith({
      refit_on_development: true,
      tuning: {
        schema_version: 1,
        trial_count: 20,
        seed: 42,
        metric: "gini",
        search_space: STARTER_SEARCH_SPACE,
      },
    })
  })

  it("selecting Fixed parameters disables tuning without changing fixed params", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(
      <Harness
        tuning={{
          schema_version: 1,
          trial_count: 20,
          seed: 42,
          metric: "gini",
          search_space: STARTER_SEARCH_SPACE,
        }}
        onUpdate={onUpdate}
      />,
    )

    expect(screen.queryByLabelText("CatBoost hyperparameters JSON")).toBeNull()
    expect(screen.getByLabelText("CatBoost search space JSON")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("radio", { name: "Fixed parameters" }))

    expect(onUpdate).toHaveBeenCalledWith("tuning", null)
    expect(onUpdate).not.toHaveBeenCalledWith("params", expect.anything())
  })

  it("shows only tuning settings and the compact search-space editor in tune mode", () => {
    render(
      <Harness
        tuning={{
          schema_version: 1,
          trial_count: 20,
          seed: 42,
          metric: "gini",
          search_space: STARTER_SEARCH_SPACE,
        }}
      />,
    )

    expect(screen.queryByLabelText("CatBoost hyperparameters JSON")).toBeNull()
    expect(screen.getByLabelText("Tuning trial count")).toBeInTheDocument()
    expect(screen.getByLabelText("Tuning seed")).toBeInTheDocument()
    expect(screen.getByLabelText("Tuning selection metric")).toBeInTheDocument()
    expect(screen.getByLabelText("CatBoost search space JSON")).toHaveValue(
      FORMATTED_STARTER_SEARCH_SPACE,
    )
    expect(screen.queryByLabelText("Search space format help")).toBeNull()
    expect(screen.getByText(/101 total fits/)).toBeInTheDocument()
    expect(screen.queryByText(/One key per CatBoost parameter/)).toBeNull()
    expect(screen.queryByRole("button", { name: "Apply" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Revert" })).toBeNull()
  })

  it("autosaves valid Search space JSON", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(
      <Harness
        tuning={{
          schema_version: 1,
          trial_count: 20,
          seed: 42,
          metric: "gini",
          search_space: {},
        }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.change(screen.getByLabelText("CatBoost search space JSON"), {
      target: {
        value: '{\n  "depth": [4, 6, 8, 10]\n}',
      },
    })

    expect(onUpdate).toHaveBeenCalledWith("tuning", {
      schema_version: 1,
      trial_count: 20,
      seed: 42,
      metric: "gini",
      search_space: {
        depth: [4, 6, 8, 10],
      },
    })
    expect(screen.queryByRole("button", { name: "Apply search space" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Revert search space" })).toBeNull()
  })

  it.each(["{invalid", "[]", "null"])(
    "keeps an invalid or non-object Search space draft local: %s",
    (draft) => {
      const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
      render(
        <Harness
          tuning={{
            schema_version: 1,
            trial_count: 20,
            seed: 42,
            metric: "gini",
            search_space: STARTER_SEARCH_SPACE,
          }}
          onUpdate={onUpdate}
        />,
      )

      fireEvent.change(screen.getByLabelText("CatBoost search space JSON"), {
        target: { value: draft },
      })

      expect(screen.getByLabelText("CatBoost search space JSON")).toHaveValue(draft)
      expect(onUpdate).not.toHaveBeenCalled()
      expect(screen.getByRole("alert")).toBeInTheDocument()
    },
  )

  it("preserves zero as a valid deterministic tuning seed", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(
      <Harness
        tuning={{
          schema_version: 1,
          trial_count: 20,
          seed: 42,
          metric: "gini",
          search_space: {},
        }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.change(screen.getByLabelText("Tuning seed"), {
      target: { value: "0" },
    })
    fireEvent.blur(screen.getByLabelText("Tuning seed"))

    expect(onUpdate).toHaveBeenCalledWith("tuning", {
      schema_version: 1,
      trial_count: 20,
      seed: 0,
      metric: "gini",
      search_space: {},
    })
  })

  it("keeps an invalid trial count local with an error", () => {
    const onUpdate = vi.fn<OnUpdateConfig>(() => ({ ok: true }))
    render(
      <Harness
        tuning={{
          schema_version: 1,
          trial_count: 20,
          seed: 42,
          metric: "gini",
          search_space: {},
        }}
        onUpdate={onUpdate}
      />,
    )

    fireEvent.change(screen.getByLabelText("Tuning trial count"), {
      target: { value: "0" },
    })

    fireEvent.blur(screen.getByLabelText("Tuning trial count"))
    expect(onUpdate).not.toHaveBeenCalled()
    expect(screen.getByRole("alert")).toBeInTheDocument()
  })
})
