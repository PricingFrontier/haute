/** The one check of each ISO 8601 calendar form the browser validates. */

const DAY = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/
const TIME = /^(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$/

/**
 * A real calendar day written YYYY-MM-DD, from year 1 (the Gregorian calendar
 * throughout, with no `Date` in between to read years before 100 as the 1900s).
 */
export function isDay(text: string): boolean {
  if (!DAY.test(text)) return false
  const [year, month, day] = text.split("-").map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
  return day <= days
}

/** A real time of day HH:MM:SS, with optional fraction and a Z or ±HH:MM offset. */
export function isTime(text: string): boolean {
  const match = TIME.exec(text)
  if (match === null) return false
  if (Number(match[1]) > 23 || Number(match[2]) > 59 || Number(match[3]) > 59) return false
  const offset = /([+-])(\d{2}):(\d{2})$/.exec(text)
  return offset === null || (Number(offset[2]) <= 23 && Number(offset[3]) <= 59)
}

/** A real day and time joined by `T`, as `isDay` and a time of day each require. */
export function isDateTime(text: string): boolean {
  const separator = text.indexOf("T")
  return separator > 0 && isDay(text.slice(0, separator)) && isTime(text.slice(separator + 1))
}
