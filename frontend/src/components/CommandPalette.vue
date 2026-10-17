<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Dialog } from 'frappe-ui'
import { ListboxFilter } from 'reka-ui'
import Icon from '@/components/Icon.vue'
import { Command, CommandGroup, CommandItem, CommandList } from '@/components/ui/command'
import { call } from '@/lib/api'
import { session } from '@/lib/session'
import { useDebouncedSearch } from '@/lib/useDebouncedSearch'
import {
  PALETTE_MIN_QUERY,
  forgetRecent,
  matchItems,
  paletteActions,
  palettePages,
  paletteReports,
  readRecents,
  recordRecent,
  serverGroups,
} from '@/lib/paletteSources'

// Plan 2026-10-17-001 U5 (R7, KTD2). Jump to a permitted page, action,
// person, request or report. Loaded only through AppShell's async component
// (chunk `CommandPalette-<hash>.js`), so none of components/ui reaches the
// eager chunk (src/lib/uiComponents.test.js).
//
// Hosted in frappe-ui's Dialog, not shadcn's CommandDialog: one focus trap,
// one Escape and the overlay z-index the tab bar needs. Its title and
// `body-content` slot stay, so `aria-labelledby` survives.
//
// Every list here is matched by lib/paletteSources.js, never by Command's own
// client filter -- server rows match on fields the row does not print (an
// employee id, a work email), and that filter would hide them. So the input
// is reka-ui's ListboxFilter on our own model: arrow keys and Enter still
// drive the list, and Command's filter state stays empty.
const open = defineModel({ type: Boolean, default: false })

const router = useRouter()
const query = ref('')
const typed = computed(() => query.value.trim())
const filter = ref(null)

const pages = computed(() => palettePages(session))
const actions = computed(() => paletteActions(session))
const recents = ref([])

// Reports: one `get_report_catalog` read per palette session, only for a
// session that can run one. A failure just leaves reports out; the next open
// asks again.
const catalog = ref(null)
let catalogAsked = false
function loadCatalog() {
  if (catalogAsked || !session.canRunReports) return
  catalogAsked = true
  call('helixhr.api.get_report_catalog')
    .then((rows) => {
      catalog.value = rows
    })
    .catch(() => {
      catalogAsked = false
    })
}

// People and requests: from two characters, 250 ms after the last key, and
// only the newest answer is shown (useDebouncedSearch's sequence token).
const server = useDebouncedSearch((q) => call('helixhr.api.portal_search', { q }))
watch(typed, (q) => {
  if (q.length >= PALETTE_MIN_QUERY) {
    server.search(q)
    return
  }
  server.cancel()
  server.results.value = null
  server.error.value = null
})
const asksServer = computed(() => typed.value.length >= PALETTE_MIN_QUERY)
const searching = computed(() => asksServer.value && server.loading.value)
const serverFailed = computed(
  () =>
    asksServer.value &&
    !server.loading.value &&
    (!!server.error.value || (server.results.value?.failed?.length ?? 0) > 0),
)

const groups = computed(() => {
  if (!typed.value) {
    return [
      { id: 'recent', heading: 'Recent', items: recents.value },
      { id: 'suggested', heading: 'Suggested', items: actions.value },
    ].filter((group) => group.items.length)
  }
  return [
    { id: 'pages', heading: 'Pages', items: matchItems(pages.value, typed.value) },
    { id: 'actions', heading: 'Actions', items: matchItems(actions.value, typed.value) },
    { id: 'reports', heading: 'Reports', items: matchItems(paletteReports(catalog.value), typed.value).slice(0, 5) },
    ...(asksServer.value ? serverGroups(server.results.value, session) : []),
  ].filter((group) => group.items.length)
})

// "No results" only once the server has settled, and never beside a failure:
// a broken search is not an empty one.
const statusLine = computed(() => {
  if (searching.value) return 'Searching people and requests…'
  if (serverFailed.value) return "Couldn't search people and requests"
  if (typed.value && !groups.value.length) return `No results for “${typed.value}”`
  return ''
})

// The panel sits at the top on a phone (the scoped style below) and its list
// is sized against the visual viewport, so results stay above the keyboard.
const viewportHeight = ref(0)
function measure() {
  viewportHeight.value = Math.round(window.visualViewport?.height || window.innerHeight)
}
const listStyle = computed(() =>
  viewportHeight.value ? { maxHeight: `min(26rem, calc(${viewportHeight.value}px - 10rem))` } : {},
)

function focusInput() {
  nextTick(() => requestAnimationFrame(() => filter.value?.$el?.focus?.()))
}

watch(
  open,
  (isOpen) => {
    if (!isOpen) {
      window.visualViewport?.removeEventListener('resize', measure)
      return
    }
    query.value = ''
    recents.value = readRecents(session)
    loadCatalog()
    measure()
    window.visualViewport?.addEventListener('resize', measure)
    focusInput()
  },
  { immediate: true },
)

// KTD13: a recent that now lands on a refusal or a missing record is dropped.
// The page shows its own forbidden / not-found state; this only watches for
// it, briefly, on the page the recent opened.
const REFUSED = '[data-async-state$=":forbidden"], [data-async-state$=":unavailable"]'
const RECORD_TYPES = ['person', 'request', 'inbox', 'report']
let stopLanding = null

function watchLanding(route) {
  stopLanding?.()
  const forget = () => {
    forgetRecent(session.user, route)
    stopLanding?.()
  }
  const landed = router.currentRoute.value
  if (landed.name === 'NotFound' || landed.path !== router.resolve(route).path) {
    forget()
    return
  }
  const main = document.getElementById('main')
  if (!main) return
  const check = () => {
    if (main.querySelector(REFUSED)) forget()
  }
  const observer = new MutationObserver(check)
  observer.observe(main, { subtree: true, childList: true, attributeFilter: ['data-async-state'] })
  const timer = setTimeout(() => stopLanding?.(), 8000)
  const offRoute = router.afterEach(() => stopLanding?.())
  stopLanding = () => {
    observer.disconnect()
    clearTimeout(timer)
    offRoute()
    stopLanding = null
  }
  check()
}

async function choose(item) {
  recordRecent(session.user, item)
  open.value = false
  const fromRecent = item.id.startsWith('recent:')
  try {
    await router.push(item.route)
  } catch {
    return
  }
  if (fromRecent && RECORD_TYPES.includes(item.type)) watchLanding(item.route)
}

onBeforeUnmount(() => {
  stopLanding?.()
  window.visualViewport?.removeEventListener('resize', measure)
})
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title: 'Search', size: 'xl' }"
  >
    <template #body-content>
      <Command data-testid="command-palette">
        <!-- The row is the field: the input draws no box of its own (the
             forms reset gave it one that framed the text but not the
             glyph), and focus darkens the row's rule instead. -->
        <div class="flex items-center gap-2 border-b border-outline-gray-2 px-1 focus-within:border-heading">
          <Icon
            name="search"
            size="h-4 w-4"
            class="shrink-0 text-ink-gray-5"
          />
          <ListboxFilter
            ref="filter"
            v-model="query"
            auto-focus
            maxlength="60"
            aria-label="Search pages, people and requests"
            placeholder="Pages, people, requests, reports"
            autocomplete="off"
            enterkeyhint="go"
            data-testid="palette-input"
            class="h-11 w-full border-0 bg-transparent px-1 text-base text-ink-gray-9 outline-none placeholder:text-ink-gray-5 focus:ring-0"
          />
        </div>
        <CommandList
          class="mt-1"
          :style="listStyle"
        >
          <CommandGroup
            v-for="group in groups"
            :key="group.id"
            :heading="group.heading"
            :data-testid="`palette-group-${group.id}`"
          >
            <CommandItem
              v-for="item in group.items"
              :key="item.id"
              :value="item.id"
              class="min-h-11 cursor-pointer"
              @select="choose(item)"
            >
              <Icon
                :name="item.icon"
                size="h-4 w-4"
                class="text-ink-gray-5"
              />
              <span class="min-w-0 flex-1">
                <span class="block truncate text-ink-gray-9">{{ item.label }}</span>
                <span
                  v-if="item.hint"
                  class="block truncate text-xs text-ink-gray-6"
                >{{ item.hint }}</span>
              </span>
            </CommandItem>
          </CommandGroup>
        </CommandList>
        <!-- Always in the page, so the live region exists before it speaks. -->
        <p
          role="status"
          class="px-2 pt-2 text-sm text-ink-gray-6"
          :class="statusLine ? '' : 'sr-only'"
          data-testid="palette-status"
        >
          {{ statusLine }}
        </p>
      </Command>
    </template>
  </Dialog>
</template>

<style>
/* KTD2: on a phone every other overlay is a bottom sheet (index.css); the
   palette is pinned to the top instead, so the keyboard never covers its
   results. Scoped by the Dialog's own `data-dialog` title. */
@media (max-width: 767px) {
  .dialog-overlay[data-dialog='Search'] > div {
    justify-content: flex-start;
  }

  .dialog-overlay[data-dialog='Search'] .dialog-content {
    border-radius: 0 0 16px 16px;
    padding-bottom: 0;
  }

  .dialog-overlay[data-dialog='Search'] .dialog-content::before {
    display: none;
  }
}
</style>
