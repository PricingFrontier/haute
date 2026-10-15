/**
 * The turn queue and the newest request's ticket (specs/frontend-shared): work run one
 * after another, a failure passing the turn on; a ticket out of date once a newer one is
 * taken or the scope is left, and read without taking one.
 */
import { describe, expect, it } from "vitest"
import { latest } from "../latest"
import { serial } from "../serial"

describe("serial", () => {
  it("runs each piece of work once the one before has settled, a failure passing the turn on", async () => {
    const turn = serial()
    const ran: string[] = []
    let finishFirst: () => void = () => undefined
    const first = turn(
      () =>
        new Promise<string>((resolve) => {
          ran.push("first starts")
          finishFirst = () => {
            ran.push("first ends")
            resolve("one")
          }
        }),
    )
    const second = turn(async () => {
      ran.push("second")
      throw new Error("refused")
    })
    const third = turn(async () => {
      ran.push("third")
      return 3
    })
    await Promise.resolve()
    expect(ran).toEqual(["first starts"])

    finishFirst()
    expect(await first).toBe("one")
    await expect(second).rejects.toThrow("refused")
    expect(await third).toBe(3)
    expect(ran).toEqual(["first starts", "first ends", "second", "third"])
  })
})

describe("latest", () => {
  it("keeps only the newest ticket current, read without taking one, and none once bumped", () => {
    const asked = latest()
    const first = asked.take()
    expect(asked.is(first)).toBe(true)
    const second = asked.take()
    expect(asked.is(first)).toBe(false)
    expect(asked.current()).toBe(second)
    // Read without taking: the newest stays current.
    expect(asked.is(asked.current())).toBe(true)

    asked.bump()
    expect(asked.is(second)).toBe(false)
    expect(asked.take()).toBeGreaterThan(second)
  })
})
