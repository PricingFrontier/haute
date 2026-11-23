/**
 * A keyed write's hold (specs/frontend-shared, "API client"): a 503 that refused the write
 * before anything was written settles its first send, and never a send again of a write
 * held with its fate unknown, which may have landed before the refusal.
 */
import { describe, expect, it } from "vitest"
import { ApiError } from "../client"
import { keyedWriteSettled, memoryHold, sendHeld } from "../keyedWrites"

interface Write {
  operation: string
  values: string
}

const refusedUnwritten = (): ApiError => {
  const detail = { error_code: "quote_store_mismatch", message: "The dev database doesn't match the form." }
  return new ApiError("HTTP 503", 503, detail.message, { detail }, detail)
}

const unanswered = (): ApiError => new ApiError("HTTP 502", 502)

const hold = () =>
  memoryHold<Write>(
    (write) => write.operation,
    (write) => write.values,
  )

const write: Write = { operation: "operation-1", values: "{}" }

describe("a keyed write refused before anything was written", () => {
  it("is let go when the refusal answers its first send", async () => {
    const held = hold()
    const refusal = refusedUnwritten()

    await expect(sendHeld(held, write, () => Promise.reject(refusal))).rejects.toBe(refusal)

    expect(keyedWriteSettled(refusal)).toBe(true)
    expect(held.held()).toBeNull()
  })

  it("stays held when the refusal answers it sent again after its fate was unknown", async () => {
    const held = hold()
    await expect(sendHeld(held, write, () => Promise.reject(unanswered()))).rejects.toBeInstanceOf(ApiError)
    expect(held.held()).toEqual(write)
    const refusal = refusedUnwritten()

    await expect(sendHeld(held, write, () => Promise.reject(refusal))).rejects.toBe(refusal)

    // The first send may have landed: the refusal says nothing of it, so the write stays.
    expect(keyedWriteSettled(refusal)).toBe(false)
    expect(held.held()).toEqual(write)
    await expect(sendHeld(held, { operation: "operation-2", values: "{}" }, () => Promise.resolve("sent"))).rejects.toThrow(
      "Another keyed write is held",
    )
  })

  it("is let go when it is sent again and answered", async () => {
    const held = hold()
    await expect(sendHeld(held, write, () => Promise.reject(unanswered()))).rejects.toBeInstanceOf(ApiError)
    await expect(sendHeld(held, write, () => Promise.reject(refusedUnwritten()))).rejects.toBeInstanceOf(ApiError)

    await expect(sendHeld(held, write, () => Promise.resolve("landed"))).resolves.toBe("landed")

    expect(held.held()).toBeNull()
  })
})
