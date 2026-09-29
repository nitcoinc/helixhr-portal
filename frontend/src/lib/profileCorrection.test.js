import { describe, it, expect } from 'vitest'
import { NOT_RECORDED, SUBJECT_MAX, correctionDraft, displayValue, spokenValue } from './profileCorrection'

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
