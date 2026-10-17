<script setup>
import { computed, nextTick, onUpdated, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { tabIndexForKey } from '@/lib/tabKeys'

// Plan 2026-10-17-001 U3 (R17; MH-01, AI-07, AI-09, AI-14, HR-09). The one
// tab strip. Ten pages each carried their own copy, with three active-tab
// styles and three keyboard models between them; this is Requests' tablist
// (the most complete of them) lifted out whole.
//
// Two modes:
//   - a `role="tablist"` (default) for views of one page, where the
//     selection lives in `?tab=` or local state. Roving tabindex, automatic
//     activation: ArrowLeft/Right move and select, Home/End jump.
//   - `nav` for route-segment sections (/settings/<key>, /profile/<key>):
//     links with `aria-current="page"`. Every link stays in the Tab order,
//     as links in a nav do; the arrow keys and Home/End only move focus.
//
// A bar with one tab is not a choice, so it renders nothing (AI-09).
// Overflow scrolls on one line with an edge fade rather than wrapping or
// clipping (HR-09, AI-14), and the selected tab is scrolled into view.
const props = defineProps({
  /** `{ key, label, count?, to?, testid?, ariaLabel?, class? }`. `count`
   * renders as a pill when it is not null; `to` is the route in `nav` mode;
   * `class` is extra classes for that one tab. */
  tabs: { type: Array, required: true },
  modelValue: { type: [String, Number], default: '' },
  /** The strip's accessible name. */
  label: { type: String, required: true },
  nav: { type: Boolean, default: false },
  /** The panel the tabs control. Tab ids become `${panelId}-tab-${key}`, so
   * the panel can name its tab with `aria-labelledby`. */
  panelId: { type: String, default: '' },
  /** `line` on paper and cards; `field` is the week's day picker, equal
   * columns on the deep field. */
  variant: { type: String, default: 'line', validator: (value) => ['line', 'field'].includes(value) },
})
const emit = defineEmits(['update:modelValue'])

// Far enough in from the edge that a revealed tab clears the fade.
const FADE_PX = 32

const root = ref(null)
const tabEls = []
const fadeLeft = ref(false)
const fadeRight = ref(false)
const line = computed(() => props.variant === 'line')

function setTabEl(index, el) {
  tabEls[index] = el?.$el ?? el
}

function isSelected(tab) {
  return tab.key === props.modelValue
}
// The tab in the Tab order: the selected one, or the first while nothing
// matches, so a keyboard can always reach the strip.
const focusIndex = computed(() => Math.max(0, props.tabs.findIndex(isSelected)))

/** "Team inbox (3)" rather than the content's run-together "Team inbox3";
 * sighted readers get the pill. */
function nameOf(tab) {
  if (tab.ariaLabel) return tab.ariaLabel
  return tab.count != null ? `${tab.label} (${tab.count})` : undefined
}

function select(tab) {
  if (!isSelected(tab)) emit('update:modelValue', tab.key)
}

function onKeydown(event, index) {
  // Alt+ArrowLeft is the browser's Back; leave every chord alone.
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
  const next = tabIndexForKey(event.key, index, props.tabs.length)
  if (next < 0) return
  event.preventDefault()
  if (!props.nav) select(props.tabs[next])
  // The element exists before the selection lands (a route change is
  // async), so focus moves now and the tabindex follows.
  tabEls[next]?.focus()
}

function measure() {
  const el = root.value
  if (!el || !line.value) return
  fadeLeft.value = el.scrollLeft > 1
  fadeRight.value = el.scrollLeft + el.clientWidth < el.scrollWidth - 1
}

function reducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** Scrolls the strip itself -- never the page -- until the selected tab is
 * clear of both fades. */
function revealSelected(animate) {
  const el = root.value
  const tab = tabEls[props.tabs.findIndex(isSelected)]
  if (!el || !tab || !line.value || el.scrollWidth <= el.clientWidth) return
  const left = tab.offsetLeft
  const right = left + tab.offsetWidth
  let target = null
  if (left - FADE_PX < el.scrollLeft) target = left - FADE_PX
  else if (right + FADE_PX > el.scrollLeft + el.clientWidth) target = right + FADE_PX - el.clientWidth
  if (target === null) return
  el.scrollTo({ left: Math.max(0, target), behavior: animate && !reducedMotion() ? 'smooth' : 'auto' })
}

// Keyed on the element rather than on mount: a strip that starts with one
// tab (Reports, before its catalog arrives) renders later than the component.
watch(
  root,
  (el, _previous, onCleanup) => {
    if (!el || !line.value) return
    measure()
    revealSelected(false)
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    onCleanup(() => observer.disconnect())
  },
  { flush: 'post' },
)
// A count or a label can change the strip's width without resizing it.
onUpdated(measure)
watch(
  () => props.modelValue,
  () => nextTick(() => revealSelected(true)),
)

const rootStyle = computed(() => {
  if (!line.value) return { gridTemplateColumns: `repeat(${props.tabs.length}, minmax(0, 1fr))` }
  // The hairline is an inset shadow, not a border, so the selected tab's 2px
  // bar sits on it without a negative margin -- which an overflow-x scroller
  // would clip.
  const style = { boxShadow: 'inset 0 -1px 0 var(--outline-gray-2)' }
  if (fadeLeft.value || fadeRight.value) {
    const start = fadeLeft.value ? 'transparent' : '#000'
    const end = fadeRight.value ? 'transparent' : '#000'
    style.maskImage = `linear-gradient(to right, ${start}, #000 ${FADE_PX}px, #000 calc(100% - ${FADE_PX}px), ${end})`
  }
  return style
})

function tabClass(tab) {
  const selected = isSelected(tab)
  if (!line.value) {
    return [
      'relative flex cursor-pointer flex-col items-center gap-1 border-r border-white/10 px-1 pb-2 pt-2.5 last:border-r-0 hover:bg-white/10 focus-visible:[outline-offset:-3px]',
      selected ? 'bg-white/15' : '',
      tab.class,
    ]
  }
  return [
    // The ring sits inside the tab: outside it, the scroller would clip it.
    'flex min-h-11 shrink-0 cursor-pointer items-center gap-2 whitespace-nowrap border-b-2 px-3 text-sm font-medium focus-visible:[outline-offset:-3px]',
    selected ? 'border-heading text-ink-gray-9' : 'border-transparent text-ink-gray-6 hover:text-ink-gray-9',
    tab.class,
  ]
}
</script>

<template>
  <component
    :is="nav ? 'nav' : 'div'"
    v-if="tabs.length > 1"
    ref="root"
    :role="nav ? undefined : 'tablist'"
    :aria-label="label"
    :class="
      line
        ? 'relative flex gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
        : 'grid'
    "
    :style="rootStyle"
    @scroll.passive="measure"
  >
    <component
      :is="nav ? RouterLink : 'button'"
      v-for="(tab, index) in tabs"
      :id="!nav && panelId ? `${panelId}-tab-${tab.key}` : undefined"
      :key="tab.key"
      :ref="(el) => setTabEl(index, el)"
      :to="nav ? tab.to : undefined"
      :type="nav ? undefined : 'button'"
      :role="nav ? undefined : 'tab'"
      :aria-selected="nav ? undefined : isSelected(tab) ? 'true' : 'false'"
      :aria-current="nav && isSelected(tab) ? 'page' : undefined"
      :aria-controls="!nav && panelId ? panelId : undefined"
      :aria-label="nameOf(tab)"
      :tabindex="nav ? undefined : index === focusIndex ? 0 : -1"
      :class="tabClass(tab)"
      :data-testid="tab.testid"
      @click="nav ? undefined : select(tab)"
      @keydown="onKeydown($event, index)"
    >
      <slot
        name="tab"
        :tab="tab"
        :selected="isSelected(tab)"
      >
        <span>{{ tab.label }}</span>
        <span
          v-if="tab.count != null"
          class="rounded-full bg-surface-gray-3 px-2 py-0.5 text-xs font-semibold tabular text-ink-gray-7"
        >{{ tab.count }}</span>
      </slot>
    </component>
  </component>
</template>
