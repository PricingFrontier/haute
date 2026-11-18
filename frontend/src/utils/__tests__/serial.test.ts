/**
 * The turn queue (specs/frontend-shared): work run one after another, a failure passing the
 * turn on.
 */
import { describe, expect, it } from "vitest"
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
