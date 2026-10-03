import { describe, it, expect } from 'vitest'
import {
  CORRECTION_VALUE_MAX,
  NOT_RECORDED,
  SUBJECT_MAX,
  correctionDraft,
  correctionEntryError,
  correctionParams,
  displayValue,
  spokenValue,
} from './profileCorrection'

describe('correctionDraft', () => {
  it('names the field and quotes the value the page shows', () => {
    expect(correctionDraft({ label: 'Date of birth', display: '17 Apr 1991' })).toEqual({
      subject: 'Correct my date of birth',
      details: 'My date of birth is shown as “17 Apr 1991”. It should be:\n',
    })
  })

  it('lower-cases Frappe’s title-case labels mid-sentence', () => {
    expect(correctionDraft({ label: 'Date of Birth', display: '1 Jan 1990' }).subject).toBe(
      'Correct my date of birth',
    )
    expect(correctionDraft({ label: 'PAN Number', display: '••••234F', masked: true }).subject).toBe(
      'Correct my PAN number',
    )
  })

  it('keeps an acronym as written', () => {
    expect(correctionDraft({ label: 'PAN', display: '••••234F', masked: true }).subject).toBe(
      'Correct my PAN',
    )
  })

  it('never asks for more of a masked identifier than the page shows', () => {
    const { details } = correctionDraft({ label: 'Bank account', display: '••••5678', masked: true })
    expect(details).toContain('ending in 5678')
    expect(details).toContain('don’t type the full number')
    expect(details).not.toMatch(/\d{5,}/)
  })

  it('never asks for an unrecorded identifier to be typed out', () => {
    const { details } = correctionDraft({ label: 'PAN', display: NOT_RECORDED, masked: true })
    expect(details).not.toContain('It should be')
    expect(details).toContain('don’t type the full number')
  })

  it('asks for the value when HR never recorded one', () => {
    expect(correctionDraft({ label: 'Blood group', display: NOT_RECORDED }).details).toBe(
      'My blood group isn’t recorded on my profile. It should be:\n',
    )
  })

  it('treats a table as one section, not a value', () => {
    expect(correctionDraft({ label: 'Education', table: true })).toEqual({
      subject: 'Correct my education',
      details: 'My education is missing something or has a mistake:\n',
    })
  })

  it('keeps the subject inside HR Request’s limit', () => {
    const { subject } = correctionDraft({ label: 'x'.repeat(300), display: 'y' })
    expect(subject.length).toBeLessThanOrEqual(SUBJECT_MAX)
  })
})

describe('displayValue', () => {
  it('says Not recorded for an empty value, and formats the rest', () => {
    expect(displayValue(null)).toBe(NOT_RECORDED)
    expect(displayValue('')).toBe(NOT_RECORDED)
    expect(displayValue(0)).toBe('0')
    expect(displayValue('2026-01-02', (v) => `on ${v}`)).toBe('on 2026-01-02')
  })
})

describe('spokenValue', () => {
  it('reads a masked value as its last four', () => {
    expect(spokenValue('••••1234')).toBe('ending in 1234')
    expect(spokenValue('••••')).toBe('hidden')
    expect(spokenValue('Engineering')).toBe('Engineering')
  })
})

describe('structured corrections (plan 2026-10-02-001 U14)', () => {
  const draft = correctionDraft({ label: 'Bank A/C No.', display: '••••5678', masked: true, fieldname: 'bank_ac_no' })

  it('marks a correctable field and keeps the value out of the prose', () => {
    expect(draft.correction_field).toBe('bank_ac_no')
    expect(draft.details).not.toMatch(/\d/)
    expect(correctionDraft({ label: 'Date of birth', display: '1 Jan 1990', fieldname: 'date_of_birth' }))
      .not.toHaveProperty('correction_field')
  })

  it('returns create_my_request params once both entries match', () => {
    expect(
      correctionParams(draft, { value: ' 1234 ', confirm: '1234', category: 'Profile correction', operationKey: 'k' }),
    ).toEqual({
      category: 'Profile correction',
      subject: draft.subject,
      details: draft.details,
      operation_key: 'k',
      correction_field: 'bank_ac_no',
      correction_value: '1234',
      correction_confirm: '1234',
    })
    expect(correctionParams(draft, { value: '1234', confirm: '1235' })).toBeNull()
    expect(correctionParams(draft, { value: '1234', confirm: '' })).toBeNull()
    expect(correctionParams(correctionDraft({ label: 'X', display: 'y' }), { value: 'a', confirm: 'a' })).toBeNull()
  })

  it('says a mismatch in one sentence, and nothing before the second entry', () => {
    expect(correctionEntryError('1234', '')).toBe('')
    expect(correctionEntryError('1234', '1234 ')).toBe('')
    expect(correctionEntryError('1234', '1235')).toMatch(/don’t match/)
    expect(correctionEntryError('x'.repeat(CORRECTION_VALUE_MAX + 1), '')).toBe('That value is too long.')
  })
})
