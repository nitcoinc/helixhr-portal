import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
// Plan 2026-10-17-001 U1 (R6). Two classes of bug kept coming back, and both
// fail silently: nothing errors, the pixels are just wrong.
//
//   1. A colour utility Tailwind generates no CSS for. frappe-ui registers
//      `ink-*` under textColor only, `surface-*` under backgroundColor only
//      and `outline-*` under borderColor/ringColor/divideColor only, so
//      `border-ink-gray-9` reads like a token and produces nothing -- which
//      is how four pages shipped tabs with no active indicator (MH-01). The
//      check is per *property*: a family valid for `text-` says nothing
//      about `border-`.
//   2. Signal yellow on paper. It measures 1.21:1 there (design-system.md,
//      "Two rules that are not negotiable"), so it may only appear inside the
//      field. Where it does is listed below, per file and exactly.

const require = createRequire(import.meta.url)
const SRC = path.resolve(import.meta.dirname, '..')

// Read through Tailwind's own loader (jiti), not `require`: frappe-ui's
// preset is ESM with extensionless imports that Node's loader rejects. This
// is the same resolution `vite build` uses, preset included.
const loadConfig = require('tailwindcss/loadConfig')
const resolveConfig = require('tailwindcss/resolveConfig')
const theme = resolveConfig(loadConfig(path.resolve(SRC, '..', 'tailwind.config.cjs'))).theme

/** `{ gray: { 1: …, DEFAULT: … } }` -> `[['gray-1', …], ['gray', …]]`. */
function colourEntries(scale, prefix = '') {
  return Object.entries(scale || {}).flatMap(([key, value]) => {
    const name = key === 'DEFAULT' ? prefix : prefix ? `${prefix}-${key}` : key
    return value && typeof value === 'object' ? colourEntries(value, name) : [[name, value]]
  })
}

const keys = (scale) => Object.keys(scale || {})
const COLOURS = Object.fromEntries(
  Object.entries({
    text: theme.textColor,
    bg: theme.backgroundColor,
    border: theme.borderColor,
    ring: theme.ringColor,
    divide: theme.divideColor,
  }).map(([family, scale]) => [family, new Map(colourEntries(scale))]),
)
// The same prefixes also name sizes, widths and keywords; those are not
// colours and are not this guard's business.
const NOT_COLOUR = {
  text: new Set([
    ...keys(theme.fontSize),
    ...['left', 'center', 'right', 'justify', 'start', 'end', 'ellipsis', 'clip'],
    ...['wrap', 'nowrap', 'balance', 'pretty'],
  ]),
  bg: new Set([
    ...keys(theme.backgroundPosition),
    ...keys(theme.backgroundSize),
    ...keys(theme.backgroundImage),
    ...['fixed', 'local', 'scroll', 'repeat', 'no-repeat', 'repeat-x', 'repeat-y'],
    ...['repeat-round', 'repeat-space'],
  ]),
  border: new Set([
    ...keys(theme.borderWidth),
    ...['solid', 'dashed', 'dotted', 'double', 'hidden', 'none', 'collapse', 'separate'],
  ]),
  ring: new Set([...keys(theme.ringWidth), 'inset']),
  divide: new Set([
    ...['x', 'y', 'x-reverse', 'y-reverse', 'solid', 'dashed', 'dotted', 'double', 'none'],
    ...keys(theme.divideWidth).flatMap((width) => [`x-${width}`, `y-${width}`]),
  ]),
}

/** Comments may name a broken class to explain why it is not used. */
function stripComments(source) {
  return source
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|\s)\/\/.*$/gm, ' ')
}

/** Every class-shaped token, variants (`hover:`, `sm:`) and `!` stripped.
 * A dynamic string (`bg-${tone}`) splits before the interpolation and never
 * reaches a full utility, so it is skipped rather than guessed at. */
function utilities(source) {
  return stripComments(source)
    .split(/[\s'"`{},=<>;]+/)
    .filter((token) => token && !token.includes('$'))
    .map((token) => ({ token, utility: token.split(':').pop().replace(/^!?-?/, '') }))
}

/** Colour utilities in `source` that Tailwind generates no CSS for. */
function deadColourUtilities(source) {
  const dead = []
  for (const { token, utility } of utilities(source)) {
    const match = utility.match(/^(text|bg|border|ring|divide)-(.+)$/)
    if (!match) continue
    const family = match[1]
    let rest = match[2]
    if (family === 'border') {
      const side = rest.match(/^(?:x|y|t|r|b|l|s|e)(?:-(.+))?$/)
      if (side) {
        if (!side[1]) continue
        rest = side[1]
      }
      if (rest.startsWith('spacing-')) continue
    }
    if (/^(opacity|offset|clip|origin|blend)-/.test(rest) || rest.startsWith('[')) continue
    const [colour, opacity] = rest.split('/')
    if (NOT_COLOUR[family].has(colour)) continue
    const value = COLOURS[family].get(colour)
    // frappe-ui's semantic colours are `var(--…)`, which Tailwind 3 cannot
    // split into channels, so `bg-surface-gray-2/70` generates nothing either.
    if (value !== undefined && !(opacity && String(value).startsWith('var('))) continue
    dead.push(token)
  }
  return dead
}

const SIGNAL = /^(?:text|bg|border(?:-[xytrblse])?|ring|divide|outline|fill|stroke)-signal(?:\/[\d.]+)?$/

/** How many signal-yellow colour utilities `source` uses. `signal-soft` is
 * the light tint and is legal on paper, so it is not counted. */
function signalUses(source) {
  return utilities(source).filter(({ utility }) => SIGNAL.test(utility)).length
}

// Field-surface components: every use below sits inside `.surface-field`,
// `.bg-field` or `.bg-field-deep` (8.35:1). Counts are exact on purpose -- a
// new signal use in one of these files fails until someone has looked at
// where it lands and raised the number here.
const SIGNAL_ALLOWED = {
  'components/AppShell.vue': 7, // side nav, phone app bar and tab bar
  'components/WeekSpine.vue': 3, // the dashboard's week spine
  'pages/Attendance.vue': 2, // Today / Exceptions block on the field
  'pages/Holidays.vue': 1, // countdown pill on the next-holiday field
  'pages/Leave.vue': 1, // balance bar on bg-field-deep
  'pages/Notifications.vue': 1, // unread icon tile on bg-field
  'pages/Payslips.vue': 1, // Download PDF on the latest-payslip field
  'pages/Profile.vue': 2, // identity band: avatar initials and photo error
  'pages/Requests.vue': 2, // team chip on bg-field beside a reply
  'pages/Team.vue': 2, // out-today field: This week link, waiting pill
  'pages/Timesheet.vue': 3, // phone day strip on the field
}

/** Files whose signal count differs from the allowlist. */
function signalViolations(files) {
  return Object.entries(files)
    .map(([file, source]) => [file, signalUses(source), SIGNAL_ALLOWED[file] || 0])
    .filter(([, found, allowed]) => found !== allowed)
    .map(([file, found, allowed]) => `${file}: ${found} signal utilities, ${allowed} allowed`)
}

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return /\.(vue|js)$/.test(entry.name) && !entry.name.endsWith('.test.js') ? [full] : []
  })
}

function tree() {
  return Object.fromEntries(
    sourceFiles(SRC).map((file) => [path.relative(SRC, file), fs.readFileSync(file, 'utf8')]),
  )
}

describe('dead colour utilities', () => {
  it('catches a family that is valid for text but not for border', () => {
    const fixture = `<a class="border-b-2 border-ink-gray-9 text-ink-gray-9 hover:bg-ink-gray-8">`
    expect(deadColourUtilities(fixture)).toEqual(['border-ink-gray-9', 'hover:bg-ink-gray-8'])
  })

  it('catches an opacity modifier on a CSS-variable colour', () => {
    expect(deadColourUtilities(`<td class="bg-surface-gray-2/70 bg-white/10">`)).toEqual([
      'bg-surface-gray-2/70',
    ])
  })

  it('passes real colours, sizes, widths, sides, opacity and dynamic strings', () => {
    const fixture = [
      `<div class="border-b-2 border-outline-gray-2 bg-surface-white text-ink-gray-9 text-sm">`,
      `<i class="ring-2 ring-outline-gray-3 divide-y divide-outline-gray-1 bg-field-deep/60">`,
      `<b :class="['border-t', 'bg-[url(x.png)]', \`bg-\${tone}\`, 'text-white/80']">`,
    ].join('\n')
    expect(deadColourUtilities(fixture)).toEqual([])
  })

  it('finds none in src', () => {
    const findings = Object.entries(tree()).flatMap(([file, source]) =>
      deadColourUtilities(source).map((token) => `${file}: ${token}`),
    )
    expect(findings, 'these classes generate no CSS').toEqual([])
  })
})

describe('signal yellow stays on the field', () => {
  it('catches a stray text-signal outside the allowlist', () => {
    const stray = `<p class="mt-1 text-sm text-signal" role="alert">`
    expect(signalViolations({ 'pages/Settings.vue': stray })).toEqual([
      'pages/Settings.vue: 1 signal utilities, 0 allowed',
    ])
    expect(signalUses(`<span class="bg-signal-soft">`)).toBe(0)
  })

  it('catches a new signal use in an allowlisted file', () => {
    const extra = `<i class="bg-signal text-signal border-signal">`
    expect(signalViolations({ 'pages/Holidays.vue': extra })).toEqual([
      'pages/Holidays.vue: 3 signal utilities, 1 allowed',
    ])
  })

  it('uses signal only where the allowlist says, and exactly as often', () => {
    expect(signalViolations(tree())).toEqual([])
  })
})
