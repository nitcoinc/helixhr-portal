import { describe, it, expect } from 'vitest'
import {
  entryErrors,
  groupStates,
  initialForm,
  missingCore,
  newOperationKey,
  placeErrors,
  routes,
  seenStates,
  stagedChanges,
} from './profileChanges'

const f = (fieldname, group, value = null, masked = false) => ({
  fieldname,
  label: fieldname,
  value,
  masked,
  change: { group, type: 'Data', options: null },
})

const blankBank = [f('salary_mode', 'bank'), f('bank_name', 'bank'), f('bank_ac_no', 'bank', null, true)]
const filledBank = [f('salary_mode', 'bank'), f('bank_name', 'bank', 'HDFC'), f('bank_ac_no', 'bank', '••••1234', true)]
const pan = [f('pan_number', 'pan', null, true)]

describe('groupStates / routes', () => {
  it('marks the bank group "goes to HR" when any bank field is on file', () => {
    const fields = [...filledBank, ...pan]
    const form = initialForm(fields)
    form.salary_mode.value = 'Bank'
    form.pan_number = { value: 'ABCDE1234F', confirm: 'ABCDE1234F' }
    const staged = stagedChanges(fields, form)
    expect(groupStates(fields)).toEqual({ bank: 'filled', pan: 'blank' })
    expect(routes(staged, fields)).toEqual({ bank: 'hr', pan: 'fill' })
    expect(seenStates(staged, fields)).toEqual({ bank: 'filled', pan: 'blank' })
  })

  it('treats a fully blank group as a fill', () => {
    const form = initialForm(blankBank)
    form.bank_name.value = 'HDFC'
    expect(routes(stagedChanges(blankBank, form), blankBank)).toEqual({ bank: 'fill' })
  })
})

describe('stagedChanges', () => {
  it('does not stage an unchanged field, nor a cleared one', () => {
    const form = initialForm(filledBank)
    expect(stagedChanges(filledBank, form)).toEqual([])
    form.bank_name.value = '  HDFC '
    expect(stagedChanges(filledBank, form)).toEqual([])
    form.bank_name.value = ''
    expect(stagedChanges(filledBank, form)).toEqual([])
  })

  it('starts a masked field empty, and stages it with its confirmation', () => {
    const form = initialForm(filledBank)
    expect(form.bank_ac_no.value).toBe('')
    form.bank_ac_no = { value: ' 9999 ', confirm: '9999' }
    expect(stagedChanges(filledBank, form)).toEqual([{ field: 'bank_ac_no', value: '9999', confirm: '9999' }])
  })
})

describe('entryErrors', () => {
  it('flags a double-entry mismatch before submit', () => {
    expect(entryErrors([{ field: 'bank_ac_no', value: '1', confirm: '2' }]).bank_ac_no).toMatch(/don’t match/)
    expect(entryErrors([{ field: 'bank_ac_no', value: '1', confirm: '' }]).bank_ac_no).toMatch(/again/)
    expect(entryErrors([{ field: 'bank_ac_no', value: '1', confirm: '1' }, { field: 'bank_name', value: 'x' }])).toEqual(
      {},
    )
  })
})

describe('missingCore', () => {
  it('names core fields a first fill lacks, only those the site has', () => {
    const form = initialForm(blankBank)
    form.bank_name.value = 'HDFC'
    expect(missingCore(stagedChanges(blankBank, form), blankBank)).toEqual({ bank: ['bank_ac_no'] })
    form.bank_ac_no = { value: '1', confirm: '1' }
    expect(missingCore(stagedChanges(blankBank, form), blankBank)).toEqual({})
  })

  it('asks nothing of a group going to HR', () => {
    const form = initialForm(filledBank)
    form.salary_mode.value = 'Cash'
    expect(missingCore(stagedChanges(filledBank, form), filledBank)).toEqual({})
  })
})

describe('placeErrors', () => {
  it('puts each refusal at its field, group or the top of the form', () => {
    const placed = placeErrors(
      [
        { field: 'bank_ac_no', group: null, message: 'a' },
        { field: 'password', group: null, message: 'b' },
        { field: null, group: 'bank', message: 'c' },
        { field: null, group: null, message: 'd' },
        { field: 'unknown', group: null, message: 'e' },
      ],
      ['bank_ac_no', 'password'],
    )
    expect(placed).toEqual({
      fields: { bank_ac_no: ['a'], password: ['b'] },
      groups: { bank: ['c'] },
      form: ['d', 'e'],
    })
  })
})

describe('newOperationKey', () => {
  it('matches the server’s key pattern', () => {
    expect(newOperationKey()).toMatch(/^[A-Za-z0-9-]{16,64}$/)
  })
})
