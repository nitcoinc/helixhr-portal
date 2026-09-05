import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  addCalendarDays,
  configureCalendar,
  formatDate,
  formatDateRange,
  formatDateTime,
  formatTime,
  isCalendarDate,
  mondayOf,
  resetCalendar,
  todayInZone,
  weekBounds,
} from './dates'

// P2-U2 / P2-R5 / P2-AE3. Characterization of the one local-calendar rule
// the whole portal has to agree on:
//
//   * a date-only string ("2026-09-03") is a calendar value. It is the
//     third of September in every timezone, on every host, forever. It is
//     never an instant, so it is never converted.
//   * a true timestamp ("2026-09-03 18:47:46.417663") is a wall-clock
//     reading in the *site's* timezone -- that is what Frappe stores --
//     and is rendered in the *authenticated user's* timezone.
//   * "today" and the Monday..Sunday week come from the authoritative user
//     timezone the server sent in the portal bootstrap, never from the
//     browser's own zone.
//
// The host's TZ is deliberately moved around inside these tests: every
// assertion below must hold identically in Kolkata, New York and Los
// Angeles, because the browser's zone is not an input to any of it.
const HOST_ZONES = ['Asia/Kolkata', 'America/New_York', 'America/Los_Angeles']
// Rendering stays browser-locale in production (the India and US offices
// each want their own day/month order), so these tests pin one locale and
// assert the *calendar*, not the copy. Copy is the design system's business.
const LOCALE = 'en-GB'
const originalTZ = process.env.TZ

function withHostZone(zone, fn) {
  process.env.TZ = zone
  try {
    fn()
  } finally {
    process.env.TZ = originalTZ
  }
}

beforeEach(() => resetCalendar())
afterEach(() => {
  process.env.TZ = originalTZ
  resetCalendar()
})

describe('date-only values are calendar values (P2-R5)', () => {
  it('renders the same day whatever the host and user zones are', () => {
    for (const hostZone of HOST_ZONES) {
      withHostZone(hostZone, () => {
        for (const userZone of HOST_ZONES) {
          configureCalendar({
            timeZone: userZone,
            systemTimeZone: 'Asia/Kolkata',
            locale: LOCALE,
          })
          expect(formatDate('2026-09-03'), `${hostZone}/${userZone}`).toBe('3 Sept 2026')
          // 1 Jan is the value that shifts to 31 Dec of the previous year
          // the moment a date-only string is parsed as UTC midnight and
          // read back in a negative offset.
          expect(formatDate('2026-01-01'), `${hostZone}/${userZone}`).toBe('1 Jan 2026')
        }
      })
    }
  })

  it('keeps a leave range on its own dates', () => {
    configureCalendar({
      timeZone: 'America/Los_Angeles',
      systemTimeZone: 'Asia/Kolkata',
      locale: LOCALE,
    })
    expect(formatDateRange('2026-09-01', '2026-09-07')).toBe('1 Sept – 7 Sept 2026')
    expect(formatDateRange('2026-09-03', '2026-09-03')).toBe('3 Sept 2026')
    expect(formatDateRange('2025-12-29', '2026-01-04')).toBe('29 Dec 2025 – 4 Jan 2026')
  })

  it('recognises the two shapes Frappe sends', () => {
    expect(isCalendarDate('2026-09-03')).toBe(true)
    expect(isCalendarDate('2026-09-03 18:47:46.417663')).toBe(false)
    expect(isCalendarDate('')).toBe(false)
    expect(isCalendarDate(null)).toBe(false)
  })
})

describe('week boundaries are Monday..Sunday calendar arithmetic (P2-R5)', () => {
  it('anchors every day of a week on the same Monday', () => {
    // 2026-09-07 is a Monday.
    const week = ['2026-09-07', '2026-09-08', '2026-09-11', '2026-09-13']
    for (const day of week) {
      expect(mondayOf(day), day).toBe('2026-09-07')
    }
    // Sunday belongs to the week that started six days earlier, not to the
    // one starting tomorrow. This is the Sunday/Monday boundary in AE3.
    expect(mondayOf('2026-09-06')).toBe('2026-08-31')
    expect(weekBounds('2026-09-06')).toEqual({ start: '2026-08-31', end: '2026-09-06' })
    expect(weekBounds('2026-09-07')).toEqual({ start: '2026-09-07', end: '2026-09-13' })
  })

  it('crosses month and year ends', () => {
    expect(weekBounds('2026-01-01')).toEqual({ start: '2025-12-29', end: '2026-01-04' })
    expect(addCalendarDays('2026-02-28', 1)).toBe('2026-03-01')
    expect(addCalendarDays('2024-02-28', 1)).toBe('2024-02-29')
    expect(addCalendarDays('2026-03-01', -1)).toBe('2026-02-28')
  })

  it('does not depend on the host timezone', () => {
    for (const hostZone of HOST_ZONES) {
      withHostZone(hostZone, () => {
        expect(mondayOf('2026-09-06'), hostZone).toBe('2026-08-31')
        expect(addCalendarDays('2026-09-06', 1), hostZone).toBe('2026-09-07')
      })
    }
  })

  it('spans a DST change without losing or gaining a day', () => {
    // US DST ends 2026-11-01; that week must still be seven calendar days.
    expect(weekBounds('2026-11-01')).toEqual({ start: '2026-10-26', end: '2026-11-01' })
    expect(addCalendarDays('2026-11-01', -1)).toBe('2026-10-31')
    // and starts 2026-03-08.
    expect(weekBounds('2026-03-08')).toEqual({ start: '2026-03-02', end: '2026-03-08' })
  })
})

describe('"today" comes from the authoritative user timezone (P2-AE3)', () => {
  // 2026-09-06 18:40 UTC. Already Monday 7 September in Kolkata, still
  // Sunday 6 September in New York and Los Angeles -- the exact instant
  // where a browser-derived "today" puts an employee in the wrong week.
  const INSTANT = Date.UTC(2026, 8, 6, 18, 40, 0)

  it('disagrees between zones exactly where the calendar does', () => {
    expect(todayInZone('Asia/Kolkata', INSTANT)).toBe('2026-09-07')
    expect(todayInZone('America/New_York', INSTANT)).toBe('2026-09-06')
    expect(todayInZone('America/Los_Angeles', INSTANT)).toBe('2026-09-06')
  })

  it('puts each user in the week their own calendar says', () => {
    expect(weekBounds(todayInZone('Asia/Kolkata', INSTANT)).start).toBe('2026-09-07')
    expect(weekBounds(todayInZone('America/New_York', INSTANT)).start).toBe('2026-08-31')
  })

  it('crosses midnight, not the host midnight', () => {
    // 2026-09-06 04:30 UTC: past midnight in Kolkata (10:00), still the
    // 5th in both American zones.
    const midnightish = Date.UTC(2026, 8, 6, 4, 30, 0)
    withHostZone('Asia/Kolkata', () => {
      expect(todayInZone('America/Los_Angeles', midnightish)).toBe('2026-09-05')
    })
    withHostZone('America/Los_Angeles', () => {
      expect(todayInZone('Asia/Kolkata', midnightish)).toBe('2026-09-06')
    })
  })

  it('uses the bootstrap value the server sent when one is configured', () => {
    configureCalendar({
      timeZone: 'America/New_York',
      systemTimeZone: 'Asia/Kolkata',
      locale: LOCALE,
    })
    expect(todayInZone(undefined, INSTANT)).toBe('2026-09-06')
  })
})

describe('timestamps are instants, rendered in the user timezone (P2-R5)', () => {
  it('reads a naive Frappe timestamp as site wall-clock time', () => {
    // Stored by a site running on Asia/Kolkata: 18:47 IST is 13:17 UTC,
    // which is 09:17 in New York on the same calendar day.
    configureCalendar({
      timeZone: 'Asia/Kolkata',
      systemTimeZone: 'Asia/Kolkata',
      locale: LOCALE,
    })
    expect(formatTime('2026-09-03 18:47:46.417663')).toBe('18:47')

    configureCalendar({
      timeZone: 'America/New_York',
      systemTimeZone: 'Asia/Kolkata',
      locale: LOCALE,
    })
    expect(formatTime('2026-09-03 18:47:46.417663')).toBe('9:17')
    expect(formatDate('2026-09-03 18:47:46.417663')).toBe('3 Sept 2026')
  })

  it('moves the day when the instant belongs to another day for that user', () => {
    // 01:30 IST on the 4th is 16:00 on the 3rd in Los Angeles.
    configureCalendar({
      timeZone: 'America/Los_Angeles',
      systemTimeZone: 'Asia/Kolkata',
      locale: LOCALE,
    })
    expect(formatDate('2026-09-04 01:30:00')).toBe('3 Sept 2026')
  })

  it('does not read the host timezone', () => {
    for (const hostZone of HOST_ZONES) {
      withHostZone(hostZone, () => {
        configureCalendar({
      timeZone: 'America/New_York',
      systemTimeZone: 'Asia/Kolkata',
      locale: LOCALE,
    })
        expect(formatTime('2026-09-03 18:47:46.417663'), hostZone).toBe('9:17')
      })
    }
  })

  it('labels Today and Yesterday against the user calendar, not the host one', () => {
    configureCalendar({
      timeZone: 'America/New_York',
      systemTimeZone: 'Asia/Kolkata',
      today: '2026-09-03',
      locale: LOCALE,
    })
    // 2026-09-04 01:30 IST == 2026-09-03 16:00 in New York.
    expect(formatDateTime('2026-09-04 01:30:00')).toBe('Today, 16:00')
    expect(formatDateTime('2026-09-03 01:30:00')).toBe('Yesterday, 16:00')
    expect(formatDateTime('2026-08-20 01:30:00')).toBe('19 Aug, 16:00')
    expect(formatDateTime('2025-08-20 01:30:00')).toBe('19 Aug 2025, 16:00')
  })

  it('survives a DST transition in the user zone', () => {
    configureCalendar({ timeZone: 'America/New_York', systemTimeZone: 'UTC', locale: LOCALE })
    // 2026-03-08 06:59 UTC is 01:59 EST; 07:00 UTC is 03:00 EDT.
    expect(formatTime('2026-03-08 06:59:00')).toBe('1:59')
    expect(formatTime('2026-03-08 07:00:00')).toBe('3:00')
    // And the site side of the conversion honours DST too.
    configureCalendar({ timeZone: 'UTC', systemTimeZone: 'America/New_York', locale: LOCALE })
    expect(formatTime('2026-03-08 01:59:00')).toBe('6:59')
    expect(formatTime('2026-03-08 03:00:00')).toBe('7:00')
  })
})

describe('unusable input falls back instead of showing nothing', () => {
  it('returns the raw value it could not parse', () => {
    expect(formatDate('not a date')).toBe('not a date')
    expect(formatDate(null)).toBe('')
    expect(formatTime(undefined)).toBe('')
    expect(formatDateRange('2026-09-01', null)).toBe('2026-09-01')
  })

  it('falls back to the browser zone until the bootstrap arrives', () => {
    withHostZone('America/New_York', () => {
      resetCalendar()
      // No configureCalendar call: the module must still produce a usable
      // answer rather than throwing before the portal bootstrap resolves,
      // and a date-only value still must not shift a day in a negative
      // offset -- whatever the host locale spells the month.
      expect(formatDate('2026-01-01')).toMatch(/\b1\b/)
      expect(formatDate('2026-01-01')).toMatch(/2026/)
      expect(todayInZone('Asia/Kolkata')).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
  })
})
