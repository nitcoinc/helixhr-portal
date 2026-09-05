// P2-U2 / P2-R5 / P2-AE3. The portal's one local-calendar module.
//
// Frappe sends two different things that look alike, and treating them
// alike is what put "1 Jan 2026" on screen as "31 Dec 2025" for anyone
// whose browser sat west of UTC:
//
//   "2026-09-03"                  a *calendar* value -- a leave date, an
//                                 attendance day, a week start. It has no
//                                 time and no zone. It must never be
//                                 converted; the third of September is the
//                                 third of September everywhere.
//   "2026-09-03 18:47:46.417663"  a real *instant*, stored by Frappe as
//                                 naive wall-clock time in the **site's**
//                                 timezone (System Settings -> Time Zone).
//                                 It is rendered in the **user's** zone.
//
// Both zones are authoritative server values: `helixhr.api.get_portal_bootstrap`
// returns the authenticated Frappe user's configured timezone (falling back
// to the site's) plus the site timezone, and `lib/session.js` hands them to
// `configureCalendar` once per hard load. The browser's own zone is never an
// input -- an employee travelling, or a laptop with a wrong clock zone, must
// not move their week. Until the bootstrap resolves the module falls back to
// the browser zone so nothing throws on the very first paint.
//
// Everything below is calendar arithmetic on year/month/day integers, or an
// Intl format with an explicit `timeZone`. There is no `new Date(string)`
// parsed in host-local time anywhere in this file, on purpose.

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/
// "2026-09-03 18:47:46.417663" and the ISO spelling of the same thing.
const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/
// A trailing Z or ±HH:MM means the value already carries its own offset, so
// the site timezone is not the right frame for it.
const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/

const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

const calendar = {
  /** The authenticated user's IANA zone. Authoritative for "today", week
   * bounds and every rendered instant. */
  timeZone: null,
  /** The site's IANA zone. The frame a naive Frappe timestamp is in. */
  systemTimeZone: null,
  /** The server's own "today" for this user, so the browser and the server
   * cannot disagree about it even by a clock skew. */
  today: null,
  /** Left undefined in production: the India and US offices want their own
   * day/month order. Tests pin it. */
  locale: undefined,
}

/** Called once per hard load from the portal bootstrap (P2-R20). */
export function configureCalendar({ timeZone, systemTimeZone, today, locale } = {}) {
  if (timeZone) calendar.timeZone = timeZone
  if (systemTimeZone) calendar.systemTimeZone = systemTimeZone
  if (today) calendar.today = today
  if (locale !== undefined) calendar.locale = locale
}

/** Test seam, and what a sign-out resets. */
export function resetCalendar() {
  calendar.timeZone = null
  calendar.systemTimeZone = null
  calendar.today = null
  calendar.locale = undefined
}

/** The zone every user-facing date is expressed in. */
export function userTimeZone() {
  return calendar.timeZone || browserZone()
}

/** The zone Frappe's naive timestamps are wall-clock readings in. */
export function siteTimeZone() {
  return calendar.systemTimeZone || calendar.timeZone || browserZone()
}

export function isCalendarDate(value) {
  return typeof value === 'string' && DATE_ONLY.test(value)
}

// --- calendar arithmetic (no instants involved) ------------------------

function parts(value) {
  const match = typeof value === 'string' && value.match(DATE_ONLY)
  if (!match) return null
  const [, y, m, d] = match
  return { y: Number(y), m: Number(m), d: Number(d) }
}

function pad(n) {
  return String(n).padStart(2, '0')
}

function iso({ y, m, d }) {
  return `${y}-${pad(m)}-${pad(d)}`
}

/** UTC is used purely as a calendar with no daylight saving, never as a
 * timezone conversion: the value goes in and comes out as the same y/m/d. */
function toUTC({ y, m, d }) {
  return Date.UTC(y, m - 1, d, 12)
}

function fromUTC(ms) {
  const date = new Date(ms)
  return { y: date.getUTCFullYear(), m: date.getUTCMonth() + 1, d: date.getUTCDate() }
}

/** `"2026-02-28"` + 1 -> `"2026-03-01"`. Days, not hours: a DST week is
 * still seven days long. */
export function addCalendarDays(value, days) {
  const p = parts(value)
  if (!p) return value
  return iso(fromUTC(toUTC(p) + days * 86400000))
}

/** The Monday of the week containing `value` (KTD10: one week is always
 * Monday..Sunday, whatever the site's week-start setting says). */
export function mondayOf(value) {
  const p = parts(value)
  if (!p) return value
  const weekday = (new Date(toUTC(p)).getUTCDay() + 6) % 7 // Monday = 0
  return addCalendarDays(value, -weekday)
}

/** `{ start, end }` for the Monday..Sunday week containing `value`. Same
 * rule as `helixhr.utils.get_week_bounds` on the server. */
export function weekBounds(value) {
  const start = mondayOf(value)
  return { start, end: addCalendarDays(start, 6) }
}

/** The seven calendar dates of that week, Monday first. */
export function weekDates(value) {
  const { start } = weekBounds(value)
  return Array.from({ length: 7 }, (_, offset) => addCalendarDays(start, offset))
}

// --- instants ----------------------------------------------------------

const partFormatters = new Map()
function partFormatter(timeZone) {
  let formatter = partFormatters.get(timeZone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
    partFormatters.set(timeZone, formatter)
  }
  return formatter
}

/** The wall-clock reading in `timeZone` at instant `ms`, as a y/m/d/h/mi/s
 * record. Intl is the only DST database available to us (KTD9: no new
 * dependency), and this is how you read it. */
function wallClock(ms, timeZone) {
  const found = {}
  for (const part of partFormatter(timeZone).formatToParts(new Date(ms))) {
    if (part.type !== 'literal') found[part.type] = Number(part.value)
  }
  return found
}

/** `timeZone`'s offset from UTC, in ms, at instant `ms`. */
function offsetAt(ms, timeZone) {
  const w = wallClock(ms, timeZone)
  return Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute, w.second) - ms
}

/** The instant at which `timeZone`'s clock reads the given wall time. Two
 * passes because the offset itself depends on the instant: the first guess
 * lands within an hour, the second lands exactly, which is what makes the
 * DST-transition assertions in dates.test.js pass. */
function instantOfWallClock({ y, m, d, h, mi, s }, timeZone) {
  const asIfUTC = Date.UTC(y, m - 1, d, h, mi, s)
  let ms = asIfUTC - offsetAt(asIfUTC, timeZone)
  ms = asIfUTC - offsetAt(ms, timeZone)
  return ms
}

/** The instant a Frappe value denotes, or null when it is a calendar date
 * (which denotes no instant at all) or unparseable. */
function instantOf(value) {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value.getTime()
  if (typeof value !== 'string' || !value) return null
  if (DATE_ONLY.test(value)) return null
  const match = value.match(TIMESTAMP)
  if (!match) return null
  if (HAS_OFFSET.test(value.trim())) {
    const ms = new Date(value.replace(' ', 'T')).getTime()
    return Number.isNaN(ms) ? null : ms
  }
  const [, y, m, d, h, mi, s] = match
  return instantOfWallClock(
    { y: +y, m: +m, d: +d, h: +h, mi: +mi, s: s ? +s : 0 },
    siteTimeZone(),
  )
}

/** Today's calendar date in `timeZone`. `at` is a test seam; production
 * passes nothing and gets the real clock. */
export function todayInZone(timeZone, at) {
  const w = wallClock(at === undefined ? Date.now() : at, timeZone || userTimeZone())
  return iso({ y: w.year, m: w.month, d: w.day })
}

/** The user's today, preferring the value the server sent in the bootstrap
 * so the browser clock cannot disagree with the API. */
export function today() {
  return calendar.today || todayInZone(userTimeZone())
}

/** The Monday..Sunday week the user is in right now. */
export function currentWeek() {
  return weekBounds(today())
}

// --- rendering ---------------------------------------------------------

const formatters = new Map()
function formatter(options, timeZone) {
  const key = `${calendar.locale || ''}|${timeZone}|${JSON.stringify(options)}`
  let found = formatters.get(key)
  if (!found) {
    found = new Intl.DateTimeFormat(calendar.locale, { ...options, timeZone })
    formatters.set(key, found)
  }
  return found
}

const DATE_OPTS = { day: 'numeric', month: 'short', year: 'numeric' }
const DATE_NO_YEAR_OPTS = { day: 'numeric', month: 'short' }
const TIME_OPTS = { hour: 'numeric', minute: '2-digit' }

/** Renders a calendar date without ever leaving the calendar: the y/m/d is
 * rebuilt as a UTC instant and formatted in UTC, so no offset can move it. */
function renderCalendarDate(p, options) {
  return formatter(options, 'UTC').format(new Date(toUTC(p)))
}

/** Resolve any Frappe date-ish value to the y/m/d the *user* sees. */
function calendarPartsFor(value) {
  const p = parts(value)
  if (p) return p
  const ms = instantOf(value)
  if (ms === null) return null
  const w = wallClock(ms, userTimeZone())
  return { y: w.year, m: w.month, d: w.day }
}

/** "3 Sep 2026" — falls back to the raw value rather than showing nothing. */
export function formatDate(value) {
  const p = calendarPartsFor(value)
  return p ? renderCalendarDate(p, DATE_OPTS) : (value ?? '')
}

/** "18:47" in the user's timezone — for a list of check-ins that already
 * sits under a date heading. */
export function formatTime(value) {
  const ms = instantOf(value)
  if (ms === null) return value ?? ''
  return formatter(TIME_OPTS, userTimeZone()).format(new Date(ms))
}

/** "Today, 18:47" / "Yesterday, 18:47" / "3 Sep, 18:47". Recent items are
 * the ones people scan, and a relative label reads faster there. "Today" is
 * the *user's* today, not the host's. */
export function formatDateTime(value) {
  const ms = instantOf(value)
  if (ms === null) return formatDate(value)
  const w = wallClock(ms, userTimeZone())
  const p = { y: w.year, m: w.month, d: w.day }
  const time = formatter(TIME_OPTS, userTimeZone()).format(new Date(ms))
  const now = today()
  if (iso(p) === now) return `Today, ${time}`
  if (iso(p) === addCalendarDays(now, -1)) return `Yesterday, ${time}`
  const nowParts = parts(now)
  const options = nowParts && nowParts.y === p.y ? DATE_NO_YEAR_OPTS : DATE_OPTS
  return `${renderCalendarDate(p, options)}, ${time}`
}

/** "1 Sep – 7 Sep 2026", collapsing the repeated month and year. */
export function formatDateRange(from, to) {
  const a = calendarPartsFor(from)
  const b = calendarPartsFor(to)
  if (!a || !b) return [from, to].filter(Boolean).join(' – ')
  if (iso(a) === iso(b)) return renderCalendarDate(a, DATE_OPTS)
  const options = a.y === b.y ? DATE_NO_YEAR_OPTS : DATE_OPTS
  return `${renderCalendarDate(a, options)} – ${renderCalendarDate(b, DATE_OPTS)}`
}
