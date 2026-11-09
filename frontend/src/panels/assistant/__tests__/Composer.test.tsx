/**
 * The composer's draft (src/panels/assistant/Composer.tsx): it leaves the input
 * only once the store has taken the message.
 *
 * Spec: specs/frontend-assistant-ui/high-level.md — "Send-time rejections".
 */

import { useState } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import useAssistantStore, { type SendMessageOptions } from "../../../stores/useAssistantStore"
import useGraphStore from "../../../stores/useGraphStore"
import Composer from "../Composer"

function Harness() {
  const [text, setText] = useState("price by region")
  return (
    <>
      <Composer
        isInsideSubmodel={false}
        currentSourceFile="main.py"
        readOnly={false}
        text={text}
        setText={setText}
      />
      <output data-testid="draft">{text}</output>
    </>
  )
}

describe("Composer draft", () => {
  const original = useAssistantStore.getState().sendMessage

  beforeEach(() => {
    useGraphStore.setState({ dirty: false })
    useAssistantStore.setState({
      status: { configured: true, mutations_enabled: true } as never,
      turnStatus: "idle",
      pipelineSource: "main.py",
    })
  })

  afterEach(() => {
    cleanup()
    useAssistantStore.setState({ sendMessage: original })
  })

  it("keeps a draft the store refused to send", () => {
    const sendMessage = vi.fn(async () => {})
    useAssistantStore.setState({ sendMessage })
    render(<Harness />)

    fireEvent.click(screen.getByTestId("assistant-send"))

    expect(sendMessage).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("draft").textContent).toBe("price by region")
  })

  it("clears the draft once the store has taken the message", () => {
    const sendMessage = vi.fn(async (_text: string, options: SendMessageOptions) => {
      options.onSent?.()
    })
    useAssistantStore.setState({ sendMessage })
    render(<Harness />)

    fireEvent.click(screen.getByTestId("assistant-send"))

    expect(screen.getByTestId("draft").textContent).toBe("")
  })
})
