import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { cn } from './utils'
// Plan 2026-10-17-001 U2 (R20, R4, KTD3, KTD4). The copied shadcn-vue
// components under `components/ui/` are only safe on this stack while four
// things stay true, and each one fails silently when it slips:
//
//   1. They stay Tailwind 3 / plain-JS copies, mapped onto Signal. A v4-only
//      class (`w-(--x)`), an `oklch(` colour or an opacity step on a mapped
//      name (`bg-muted/50`, a hex variable Tailwind 3 cannot split) builds
//      clean and paints nothing.
//   2. tailwind-merge stays on 2.x. 3.x reads only Tailwind v4 class names,
//      so `cn()` would start dropping the wrong half of a conflict.
//   3. reka-ui is installed once. frappe-ui is built on it too; two copies
//      means two sets of injection keys, and contexts stop meeting.
//   4. None of it reaches the eager chunk (R4 JS budget): the shell and Home
//      reach these files only through async components.

const SRC = path.resolve(import.meta.dirname, '..')
const UI = path.join(SRC, 'components', 'ui')
const NODE_MODULES = path.resolve(SRC, '..', 'node_modules')

function files(dir) {
  if (!fs.existsSync(dir)) return []
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return files(full)
    return /\.(vue|js|ts)$/.test(entry.name) ? [full] : []
  })
}

// shadcn's colour vocabulary, mapped or not yet: a copy that brings in a new
// name still may not put an opacity step on it.
const SHADCN_COLOURS = [
  'background',
  'foreground',
  'card',
  'popover',
  'primary',
  'secondary',
  'muted',
  'accent',
  'destructive',
  'border',
  'input',
  'ring',
  'chart',
  'sidebar',
]
const OPACITY_ON_MAPPED = new RegExp(
  `(?:^|[\\s'"\`:])(!?-?[a-z]+(?:-[xytrblse])?-(?:${SHADCN_COLOURS.join('|')})(?:-[a-z0-9-]+)?\\/\\d+)`,
)

/** Rule breaks in one copied file's source, as readable strings. */
function uiViolations(source) {
  const found = []
  if (/from\s+['"](?:lucide-vue-next|@lucide\/vue)['"]/.test(source)) {
    found.push('imports lucide: use Icon.vue with a lib/icons.js path')
  }
  if (/<script[^>]*\blang=["']ts["']/.test(source)) found.push('lang="ts"')
  if (source.includes('(--')) found.push('Tailwind v4 `(--var)` shorthand: use `[--var]`')
  if (source.includes('@theme')) found.push('Tailwind v4 @theme')
  if (source.includes('oklch(')) found.push('oklch( colour')
  const opacity = source.match(OPACITY_ON_MAPPED)
  if (opacity) found.push(`opacity modifier on a mapped colour: ${opacity[1]}`)
  return found
}

describe('copied shadcn-vue components', () => {
  it('catches each rule on a fixture', () => {
    const fixture = [
      '<script setup lang="ts">',
      "import { Search } from 'lucide-vue-next'",
      '</script>',
      '<div class="w-(--sidebar-width) bg-muted/50 data-[open]:text-muted-foreground/80">',
      '<style>@theme { --x: oklch(0.5 0 0) }</style>',
    ].join('\n')
    expect(uiViolations(fixture)).toEqual([
      'imports lucide: use Icon.vue with a lib/icons.js path',
      'lang="ts"',
      'Tailwind v4 `(--var)` shorthand: use `[--var]`',
      'Tailwind v4 @theme',
      'oklch( colour',
      'opacity modifier on a mapped colour: bg-muted/50',
    ])
  })

  it('passes solid steps, opacity on non-mapped utilities and arbitrary variants', () => {
    const fixture = [
      '<script setup>',
      "import Icon from '@/components/Icon.vue'",
      '</script>',
      `<div class="bg-muted text-muted-foreground opacity-50 w-[--x] [&_svg:not([class*='size-'])]:size-3">`,
    ].join('\n')
    expect(uiViolations(fixture)).toEqual([])
  })

  it('finds none under components/ui', () => {
    const all = files(UI)
    expect(all.length, 'components/ui is empty -- did the copy move?').toBeGreaterThan(0)
    const findings = all.flatMap((file) =>
      uiViolations(fs.readFileSync(file, 'utf8')).map(
        (problem) => `${path.relative(SRC, file)}: ${problem}`,
      ),
    )
    expect(findings).toEqual([])
  })
})

/** Every installed copy of `name`, nested ones included, as package dirs. */
function installedCopies(name, dir = NODE_MODULES) {
  if (!fs.existsSync(dir)) return []
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (!entry.isDirectory() || entry.name.startsWith('.')) return []
    const full = path.join(dir, entry.name)
    const packages = entry.name.startsWith('@')
      ? fs.readdirSync(full).map((child) => [`${entry.name}/${child}`, path.join(full, child)])
      : [[entry.name, full]]
    return packages.flatMap(([pkg, pkgDir]) => [
      ...(pkg === name && fs.existsSync(path.join(pkgDir, 'package.json')) ? [pkgDir] : []),
      ...installedCopies(name, path.join(pkgDir, 'node_modules')),
    ])
  })
}

describe('pinned dependencies (KTD3)', () => {
  it('installs tailwind-merge 2.x', () => {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(NODE_MODULES, 'tailwind-merge', 'package.json'), 'utf8'),
    )
    expect(Number(pkg.version.split('.')[0]), `tailwind-merge ${pkg.version}`).toBeLessThan(3)
  })

  it('installs exactly one reka-ui', () => {
    const copies = installedCopies('reka-ui').map((dir) => path.relative(NODE_MODULES, dir))
    expect(copies, 'dedupe reka-ui in yarn.lock so frappe-ui and components/ui share one').toEqual([
      'reka-ui',
    ])
  })
})

const ENTRY_POINTS = ['main.js', 'App.vue', 'components/AppShell.vue', 'pages/Dashboard.vue']
const STATIC_IMPORT =
  /(?:^|[;\s])(?:import|export)\s+(?:[\w*{}\s,$]+?\s+from\s+)?['"]([^'"]+)['"]/g

/** Static (eager) import specifiers in `source`; `import()` is not one. */
function staticImports(source) {
  return [...source.matchAll(STATIC_IMPORT)].map((match) => match[1])
}

/** `specifier` as a file under src/, or null for a package. */
function resolveLocal(specifier, fromFile) {
  let base
  if (specifier.startsWith('@/')) base = path.join(SRC, specifier.slice(2))
  else if (specifier.startsWith('.')) base = path.resolve(path.dirname(fromFile), specifier)
  else return null
  const candidates = [base, `${base}.js`, `${base}.vue`, path.join(base, 'index.js')]
  return candidates.find((file) => fs.existsSync(file) && fs.statSync(file).isFile()) || null
}

/** Every src/ file reachable from the entry points through static imports. */
function eagerGraph() {
  const seen = new Set()
  const queue = ENTRY_POINTS.map((file) => path.join(SRC, file))
  while (queue.length) {
    const file = queue.pop()
    if (seen.has(file)) continue
    seen.add(file)
    if (!/\.(vue|js)$/.test(file)) continue
    for (const specifier of staticImports(fs.readFileSync(file, 'utf8'))) {
      const resolved = resolveLocal(specifier, file)
      if (resolved) queue.push(resolved)
    }
  }
  return [...seen].map((file) => path.relative(SRC, file))
}

const isAsyncOnly = (file) =>
  file.startsWith(`components${path.sep}ui${path.sep}`) ||
  file === path.join('lib', 'utils.js') ||
  path.basename(file) === 'Toaster.vue'

describe('async boundary (KTD4, R4)', () => {
  it('reads static imports and skips dynamic ones', () => {
    const source = [
      "import { cn } from '@/lib/utils'",
      "import './index.css'",
      'import {',
      '  Command,',
      "} from '@/components/ui/command'",
      "export { x } from './x'",
      "const Palette = defineAsyncComponent(() => import('@/components/CommandPalette.vue'))",
    ].join('\n')
    expect(staticImports(source)).toEqual([
      '@/lib/utils',
      './index.css',
      '@/components/ui/command',
      './x',
    ])
  })

  it('keeps components/ui, lib/utils.js and Toaster.vue out of the eager graph', () => {
    const graph = eagerGraph()
    // Not an entry point, so finding it proves the walk follows imports
    // rather than just listing ENTRY_POINTS back.
    expect(graph, 'the import walk stopped at the entry points').toContain(
      path.join('lib', 'session.js'),
    )
    expect(graph.filter(isAsyncOnly), 'load these through defineAsyncComponent').toEqual([])
  })
})

describe('cn', () => {
  it('lets the later utility win a conflict', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
  })

  it('keeps a Signal ink colour beside a frappe-ui font size', () => {
    expect(cn('text-ink-gray-8', 'text-2xs')).toBe('text-ink-gray-8 text-2xs')
  })
})
