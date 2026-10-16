// shadcn-vue `command` (new-york, Tailwind 3 registry), copied in by plan
// 2026-10-17-001 U2 and adapted per KTD4: plain JS, the search glyph drawn by
// Icon.vue, colours mapped onto Signal in tailwind.config.cjs. No
// CommandDialog: the palette is hosted in frappe-ui's Dialog (KTD2), so the
// copy carries no second dialog, focus trap or overlay. Load only through an
// async component; `src/lib/uiComponents.test.js` holds both rules.
import { createContext } from 'reka-ui'

export { default as Command } from './Command.vue'
export { default as CommandEmpty } from './CommandEmpty.vue'
export { default as CommandGroup } from './CommandGroup.vue'
export { default as CommandInput } from './CommandInput.vue'
export { default as CommandItem } from './CommandItem.vue'
export { default as CommandList } from './CommandList.vue'
export { default as CommandSeparator } from './CommandSeparator.vue'
export { default as CommandShortcut } from './CommandShortcut.vue'

export const [useCommand, provideCommandContext] = createContext('Command')

export const [useCommandGroup, provideCommandGroupContext] = createContext('CommandGroup')
