import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
// P2-U9 / P2-R24. `tailwind.config.cjs` no longer scans every frappe-ui
// component -- doing so generated 167,821 bytes of CSS against U0's
// 162,906-byte budget. It scans a named list instead, and this is what stops
// that list from silently going stale: import a new frappe-ui component and
// this fails until it is added to `FRAPPE_UI_IN_USE`, rather than shipping a
// control with no styles.

const SRC = path.resolve(import.meta.dirname, '..')

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return /\.(vue|js)$/.test(entry.name) ? [full] : []
  })
}

/** Component-shaped named imports from 'frappe-ui'. The package also exports
 * functions (createResource, setConfig, frappeRequest, resourcesPlugin);
 * those carry no template and therefore no utility classes, and are told
 * apart by their lower-case first letter. */
function importedComponents() {
  const found = new Set()
  for (const file of sourceFiles(SRC)) {
    const source = fs.readFileSync(file, 'utf8')
    for (const match of source.matchAll(/import\s*\{([^}]*)\}\s*from\s*'frappe-ui'/g)) {
      for (const name of match[1].split(',')) {
        const identifier = name.trim().split(/\s+as\s+/)[0].trim()
        if (identifier && /^[A-Z]/.test(identifier)) found.add(identifier)
      }
    }
  }
  return [...found].sort()
}

describe('tailwind content list', () => {
  it('covers every frappe-ui component this app imports', () => {
    // Read as text, not `require`d: the config pulls in frappe-ui's Tailwind
    // preset, which does not resolve under vitest's ESM loader and is not
    // what this test is about.
    const config = fs.readFileSync(path.resolve(SRC, '..', 'tailwind.config.cjs'), 'utf8')
    const list = (name) =>
      (config.match(new RegExp(`const ${name} = \\[([^\\]]*)\\]`))?.[1] || '')
        .split(',')
        .map((entry) => entry.trim().replace(/^['"]|['"]$/g, '').replace(/\.vue$/, ''))
        .filter(Boolean)
    const scanned = [...list('FRAPPE_UI_IN_USE'), ...list('FRAPPE_UI_TRANSITIVE')]
    expect(scanned.length, 'could not read the component lists out of tailwind.config.cjs')
      .toBeGreaterThan(0)
    const missing = importedComponents().filter((component) => !scanned.includes(component))
    expect(missing, 'add these to FRAPPE_UI_IN_USE in tailwind.config.cjs').toEqual([])
  })
})
