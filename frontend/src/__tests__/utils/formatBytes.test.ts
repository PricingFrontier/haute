import { describe, it, expect } from "vitest"
import { formatBytes, formatByteSize } from "../../utils/formatBytes"

describe("formatBytes", () => {
  it("returns bytes for values under 1 KB", () => {
    expect(formatBytes(0)).toBe("0 B")
    expect(formatBytes(1)).toBe("1 B")
    expect(formatBytes(512)).toBe("512 B")
    expect(formatBytes(1023)).toBe("1023 B")
  })

  it("returns KB for values from 1 KB to just under 1 MB", () => {
    expect(formatBytes(1024)).toBe("1.0 KB")
    expect(formatBytes(1536)).toBe("1.5 KB")
    expect(formatBytes(10240)).toBe("10.0 KB")
    expect(formatBytes(1024 * 1024 - 1)).toBe("1024.0 KB")
  })

  it("returns MB for values 1 MB and above", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB")
    expect(formatBytes(1.5 * 1024 * 1024)).toBe("1.5 MB")
    expect(formatBytes(100 * 1024 * 1024)).toBe("100.0 MB")
  })

  it("handles exact boundary at 1024", () => {
    expect(formatBytes(1024)).toBe("1.0 KB")
  })

  it("handles exact boundary at 1 MB", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB")
  })

  it("formats gigabytes from 1 GB", () => {
    expect(formatBytes(1024 ** 3 - 1)).toBe("1024.0 MB")
    expect(formatBytes(1024 ** 3)).toBe("1.0 GB")
    expect(formatBytes(3.8 * 1024 ** 3)).toBe("3.8 GB")
  })
})

describe("formatByteSize", () => {
  const KIB = 1024
  const MIB = 1024 * KIB
  const GIB = 1024 * MIB
  const TIB = 1024 * GIB

  it("returns bytes below 1 KB", () => {
    expect(formatByteSize(0)).toBe("0 B")
    expect(formatByteSize(1023)).toBe("1023 B")
  })

  it("climbs through every unit and drops the decimal from ten", () => {
    expect(formatByteSize(KIB)).toBe("1.0 KB")
    expect(formatByteSize(MIB)).toBe("1.0 MB")
    expect(formatByteSize(3.8 * GIB)).toBe("3.8 GB")
    expect(formatByteSize(40 * GIB)).toBe("40 GB")
    expect(formatByteSize(2 * TIB)).toBe("2.0 TB")
  })

  it("drops the decimal at ten and above, so a value and its limit stay short", () => {
    expect(formatByteSize(9.94 * MIB)).toBe("9.9 MB")
    expect(formatByteSize(512 * MIB)).toBe("512 MB")
  })

  it("applies the threshold to the printed value, not the held one", () => {
    // 9.96 would round to "10.0 MB", which beside "10 MB" reads as a bug.
    expect(formatByteSize(9.96 * MIB)).toBe("10 MB")
    expect(formatByteSize(9.94 * MIB)).toBe("9.9 MB")
  })

  it("carries a rounded value into the next unit instead of printing 1024", () => {
    // 1023.999 KB must read as 1.0 MB, not "1024 KB".
    expect(formatByteSize(MIB - 1)).toBe("1.0 MB")
    expect(formatByteSize(GIB - 1)).toBe("1.0 GB")
  })

  it("stops at the largest unit rather than inventing one", () => {
    expect(formatByteSize(5000 * TIB)).toBe("5000 TB")
  })
})
