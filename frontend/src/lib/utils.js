import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

// Plan 2026-10-17-001 U2 (KTD3, R20). The class joiner the copied shadcn-vue
// components under `components/ui/` call: clsx for conditionals, then
// tailwind-merge so a caller's `class` wins over the component's default
// (`px-2` then `px-4` leaves `px-4`). tailwind-merge stays on 2.x -- 3.x
// only understands Tailwind v4 class names.
//
// Plain twMerge is enough for the frappe-ui type scale this app uses:
// `text-2xs` .. `text-3xl` read as font sizes, so they never knock out a
// `text-ink-*` colour. frappe-ui's `text-p-*` paragraph sizes do not, and
// would; if those are ever adopted, switch to `extendTailwindMerge` with a
// `font-size` class group for them.
//
// Only reached through async components (KTD4): `uiComponents.test.js` fails
// if the shell or Home imports this eagerly.

/** Join class values, letting later Tailwind utilities override earlier ones. */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
