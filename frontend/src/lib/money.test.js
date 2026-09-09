import { describe, expect, it } from 'vitest'
import { formatMoney } from './money'

// P3-U2 / P3-R1, P3-AE2. One rule: the amount is rendered in the currency of
// the row it came from, and two rows in two currencies are never mixed.
//
// The locale is pinned here and only here: production leaves digit grouping
// to the browser, the way `lib/dates.js` leaves day/month order to it, so
// what these assertions are about is the *currency*, never the copy.
const en = { locale: 'en-US' }

describe('formatMoney', () => {
  it('renders each amount in the currency it was given', () => {
    expect(formatMoney(4500, 'USD', en)).toBe('$4,500.00')
    expect(formatMoney(81000, 'INR', en)).toBe('₹81,000.00')
    expect(formatMoney(4500, 'EUR', en)).toBe('€4,500.00')
  })

  it('drops the minor unit only when asked', () => {
    expect(formatMoney(4500.5, 'USD', en)).toBe('$4,500.50')
    expect(formatMoney(4500.5, 'USD', { ...en, decimals: false })).toBe('$4,501')
  })

  it('accepts the strings and negatives a server response can carry', () => {
    expect(formatMoney('4500', 'USD', en)).toBe('$4,500.00')
    expect(formatMoney(-250, 'USD', en)).toBe('-$250.00')
    expect(formatMoney(0, 'USD', en)).toBe('$0.00')
  })

  it('renders nothing rather than an unlabelled number', () => {
    // A figure with no currency beside it is not a payslip amount, it is a
    // number somebody will read as their own currency and be wrong about.
    expect(formatMoney(4500, null, en)).toBe('')
    expect(formatMoney(4500, '', en)).toBe('')
    expect(formatMoney(null, 'USD', en)).toBe('')
    expect(formatMoney(undefined, 'USD', en)).toBe('')
    expect(formatMoney('', 'USD', en)).toBe('')
    expect(formatMoney('nonsense', 'USD', en)).toBe('')
  })

  it('keeps the number true when the currency code is not one Intl knows', () => {
    expect(formatMoney(4500, 'NOTACODE', en)).toBe('4,500 NOTACODE')
  })
})
