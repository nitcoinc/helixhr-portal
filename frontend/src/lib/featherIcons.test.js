import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

// P2-U9 step 1. `vite.config.js` aliases `feather-icons` to
// `src/lib/featherIcons.js`, which drops 96KB from the eagerly loaded
// bundle. That trade is only safe while nothing asks a frappe-ui component
// for a named Feather glyph, so this asserts the condition rather than
// trusting a comment to be read.

const SRC = path.resolve(import.meta.dirname, '..')

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return sourceFiles(full)
    return /\.(vue|js)$/.test(entry.name) ? [full] : []
  })
}

describe('feather-icons alias', () => {
  it('is safe because no component asks frappe-ui for a named glyph', () => {
    const offenders = sourceFiles(SRC)
      .filter((file) => /(:|\s|^)(icon|icon-left|icon-right|iconLeft|iconRight)\s*=\s*["']/m.test(
        fs.readFileSync(file, 'utf8'),
      ))
      .map((file) => path.relative(SRC, file))
    expect(
      offenders,
      'add the glyph to src/lib/icons.js and use Icon.vue, or drop the alias in vite.config.js',
    ).toEqual([])
  })

  it('answers every lookup with a renderable fallback', async () => {
    const feather = await import('./featherIcons.js')
    expect(feather.icons['anything-at-all'].attrs).toBeTruthy()
    expect(Object.keys(feather.icons)).toEqual([])
  })
})
