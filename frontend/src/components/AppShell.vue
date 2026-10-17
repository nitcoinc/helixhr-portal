<script setup>
import Avatar from '@/components/Avatar.vue'
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Dialog } from 'frappe-ui'
import Icon from '@/components/Icon.vue'
import { session, signOut } from '@/lib/session'
import { currentUnread, watchUnread, unwatchUnread } from '@/lib/unread'
import { watchDialogs, unwatchDialogs } from '@/lib/dialogA11y'
import { allowedNav, askHrTarget, navTarget, tabBar } from '@/lib/nav'
import { defaultCollapsed, groupNav, railGroups, readCollapsed, writeCollapsed } from '@/lib/navGroups'
import { approvalsLabel } from '@/lib/approvalsCount'
import { toasts } from '@/lib/notify'

// Plan 2026-10-17-001 U14 (KTD4, R4). The toast UI is reka-ui's Toast, about
// 18KB, so it never rides in the eager chunk: it is fetched on the first idle
// moment after the shell paints, which puts its live regions in the page
// before the first message in the usual case. A message sent sooner waits in
// `lib/notify.js` and pulls the chunk in itself. A dedicated file means Vite
// names the chunk `Toaster-<hash>.js`, which hardening.spec.ts looks for.
const Toaster = defineAsyncComponent(() => import('@/components/Toaster.vue'))
const toasterWanted = ref(false)
let idleHandle = null
let idleTimer = null
function wantToaster() {
  toasterWanted.value = true
}
watch(
  () => toasts.length > 0,
  (pending) => pending && wantToaster(),
  { immediate: true },
)

// The destinations and their gates live in lib/nav.js (plan 2026-10-17-001
// U4), shared with the command palette so both apply the same gates.
const route = useRoute()
const router = useRouter()
const showMore = ref(false)

const unread = computed(() => currentUnread())
const unreadLabel = computed(() => (unread.value > 9 ? '9+' : String(unread.value)))
// KTD8: seeded by the bootstrap, kept by the Approvals page; never polled.
const approvals = computed(() => approvalsLabel())

/** The attention count an item carries, as its badge text, or ''. */
function badgeFor(item) {
  if (item.badge === 'unread') return unread.value > 0 ? unreadLabel.value : ''
  if (item.badge === 'approvals') return approvals.value
  return ''
}

const navItems = computed(() => allowedNav(session))
// KTD7: a stable approver's bar holds Approvals where Leave was.
const primaryItems = computed(() => tabBar(navItems.value, session))
const moreItems = computed(() => navItems.value.filter((item) => !primaryItems.value.includes(item)))
const rail = computed(() => railGroups(navItems.value, session))
const navGroups = computed(() => rail.value.groups)
const moreGroups = computed(() => groupNav(moreItems.value))
// A queue-dependent approver's Approvals lives behind More, so More carries
// its count (KTD7). An oversight-only HR User counts 0 and shows nothing.
const moreBadge = computed(() =>
  moreItems.value.some((item) => item.badge === 'approvals') ? approvals.value : '',
)
const askHr = computed(() => askHrTarget(session))

/** A collapsed group's pill: what in it needs attention, never how many
 * items it holds (AI-15) -- a count there reads as "N things need you". */
function groupAttention(group) {
  return group.items.map(badgeFor).filter(Boolean).join(' · ')
}

// R8 / KTD13: collapsed state is per user and per device. With nothing
// remembered, the session's own work group starts open and every other
// collapsible group starts closed (lib/navGroups.js); a remembered state
// always wins. The group holding the current route is shown open for this
// view only -- `routeOpen` never touches storage, so leaving the route lets
// the remembered state come back.
//
// Row budget at an 800px-tall window, in CSS px, worked from the classes
// below (frappe-ui's text-sm/xs at line-height 1.15; a 20px icon sets a row
// height). Cross-check: the same arithmetic puts main's hr rail, HR and Admin
// both collapsed, at 574 of 574 -- and the 800px spec passed it with nothing
// to spare. Chrome: brand row 64 + identity 52 + nav top margin 12 + footer
// (Ask HR / Sign out row 48, credit 30) 78 = 206, so the nav gets 594. A nav
// row is 28 + 2 gap; a labelled group adds 8 margin + 22 heading + 2; a
// collapsed group is 30 in all; Profile's group is 36.
//   hr (HR Manager + Admin, no reports): pinned 58 + HR(4) 150 + My work(5)
//     180 + Pay collapsed 30 + People & team(2) 90 + Admin collapsed 30 +
//     Profile 36 = 574 -- 20 to spare.
//   System Manager with an Employee: the same rows (Email templates sits in
//     the collapsed Admin) = 574. With direct reports, Team adds 30 = 604:
//     10 over, the one shape that scrolls on a first visit.
//   portal-admin: pinned 58 + Admin(2) 90 + HR collapsed 30 + My work 180 +
//     Pay collapsed 30 + People & team(2) 90 + Profile 36 = 514.
const collapsed = ref(new Set())
watch(
  [() => session.user, navGroups],
  () => {
    const fallback = defaultCollapsed(navGroups.value, rail.value.own)
    collapsed.value = new Set(readCollapsed(session.user, fallback))
  },
  { immediate: true },
)
const routeOpen = ref(null)
watch(
  () => [route.path, navGroups.value],
  () => {
    routeOpen.value = navGroups.value.find((g) => g.items.some(isActive))?.id ?? null
  },
  { immediate: true },
)
function isOpen(group) {
  return !group.collapsible || group.id === routeOpen.value || !collapsed.value.has(group.id)
}
function toggleGroup(group) {
  const next = new Set(collapsed.value)
  if (isOpen(group)) {
    next.add(group.id)
    if (routeOpen.value === group.id) routeOpen.value = null
  } else {
    next.delete(group.id)
  }
  collapsed.value = next
  writeCollapsed(session.user, [...next])
}

// R10 (WCAG 2.4.1, 2.4.3): after a navigation to another page -- not the
// first load, and not a detail, tab or filter inside the same page, which
// manage their own focus -- focus moves to <main>, so a screen reader starts
// at the new page rather than on the link that left the old one.
const mainEl = ref(null)
function focusMain() {
  mainEl.value?.focus({ preventScroll: true })
}
let stopFocusOnNavigate = null

const deskOnly = computed(() => session.status === 'desk-only')
// A desk-only session has no Employee to name, and no Profile page to open.
const employeeName = computed(
  () => session.employee?.employee_name || (deskOnly.value ? session.user : ''),
)
const identityRoute = computed(() => (deskOnly.value ? '/' : '/profile'))
const employeeRole = computed(() =>
  [session.employee?.designation, session.employee?.department].filter(Boolean).join(' · '),
)
const initials = computed(() =>
  employeeName.value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join(''),
)

/** `/timesheet/history` should keep the Timesheet tab lit; the root route
 * must not match everything, so it's the one exact-match item. */
function isActive(item) {
  return item.to === '/' ? route.path === '/' : route.path.startsWith(item.to)
}

// The More tab lights up when the route you are on lives behind it, so the
// tab bar never shows five unlit destinations while you are standing on
// Attendance or Profile (P2-U3 step 6).
const moreIsActive = computed(() => moreItems.value.some(isActive))

function closeMore() {
  showMore.value = false
}

onMounted(() => {
  stopFocusOnNavigate = router.afterEach((to, from, failure) => {
    if (failure || !from.matched.length || to.meta.title === from.meta.title) return
    nextTick(() => requestAnimationFrame(focusMain))
  })
  watchUnread()
  // P2-U9: names frappe-ui's unlabelled dialog close button, once for the
  // whole application. See src/lib/dialogA11y.js.
  watchDialogs()
  if (!toasterWanted.value) {
    if ('requestIdleCallback' in window) idleHandle = requestIdleCallback(wantToaster, { timeout: 3000 })
    else idleTimer = setTimeout(wantToaster, 1500)
  }
})
onUnmounted(() => {
  stopFocusOnNavigate?.()
  unwatchUnread()
  unwatchDialogs()
  if (idleHandle !== null) cancelIdleCallback(idleHandle)
  clearTimeout(idleTimer)
})
</script>

<template>
  <div class="min-h-screen bg-surface-gray-1 lg:flex">
    <!-- R10 (WCAG 2.4.1): the first focusable element on every page. -->
    <a
      href="#main"
      class="sr-only rounded-md bg-white px-4 py-2 text-sm font-medium text-ink-gray-9 shadow-lg focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:inline-flex focus:min-h-11 focus:items-center"
      @click.prevent="focusMain"
    >
      Skip to main content
    </a>
    <!-- Desktop side nav (>=1024px). Hidden on phone/tablet, where the
         bottom tab bar below takes over. -->
    <!-- The white column is the outer div so it stretches to the full
         page height on a long page; the sticky inner aside is only as
         tall as the viewport. Sticky alone left a bare grey strip below
         the nav once content scrolled past one screen. -->
    <div class="hidden shrink-0 bg-field lg:block">
      <aside class="sticky top-0 flex h-screen w-64 flex-col">
        <div class="flex items-center gap-2 px-5 py-4">
          <span
            class="flex h-8 w-8 items-center justify-center rounded-md bg-signal font-heading text-sm font-bold text-field"
          >
            H
          </span>
          <span class="font-heading text-base font-semibold text-white">HelixHR</span>
          <a
            v-if="session.deskUrl"
            :href="session.deskUrl"
            class="ml-auto inline-flex min-h-8 cursor-pointer items-center rounded-md border border-white/25 px-2.5 text-xs font-medium text-blue-100 hover:bg-white/10 hover:text-white"
          >
            Open Desk
          </a>
        </div>

        <router-link
          :to="identityRoute"
          class="mx-3 flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2 hover:bg-white/10"
        >
          <Avatar
            class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/15 font-medium text-white"
            :photo-url="session.employee?.photo_url"
            :initials="initials || '—'"
            :size="36"
            data-testid="shell-avatar"
          />
          <span class="min-w-0">
            <span class="block truncate text-sm font-medium text-white">
              {{ employeeName || 'Loading…' }}
            </span>
            <span
              v-if="employeeRole"
              class="block truncate text-xs text-blue-200"
            >
              {{ employeeRole }}
            </span>
          </span>
        </router-link>

        <nav
          class="mt-3 flex-1 overflow-y-auto px-3"
          aria-label="Main"
        >
          <div
            v-for="group in navGroups"
            :key="group.id"
            class="space-y-0.5"
            :class="group.label || group.id === 'bottom' ? 'mt-2' : ''"
          >
            <!-- A collapsed group must read as "there is more in here": HR
                 could not find Settings inside a closed Admin. So the header
                 is a visible control (hover surface, down-chevron that flips)
                 and its tooltip names what it holds. Its pill shows only what
                 needs attention inside it, never an item count (AI-15). -->
            <button
              v-if="group.collapsible"
              type="button"
              class="flex w-full cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-200 hover:bg-white/10 hover:text-white"
              :aria-expanded="isOpen(group)"
              :aria-controls="`nav-group-${group.id}`"
              :title="isOpen(group) ? `Hide ${group.label}` : `Show ${group.items.map((item) => item.label).join(', ')}`"
              data-testid="nav-group-toggle"
              @click="toggleGroup(group)"
            >
              <span class="flex items-center gap-2">
                {{ group.label }}
                <!-- Visual only: the accessible name stays the group's own
                     word, and the tooltip already lists what is inside. -->
                <span
                  v-if="!isOpen(group) && groupAttention(group)"
                  aria-hidden="true"
                  class="rounded-full bg-signal px-1.5 text-[11px] font-bold normal-case tracking-normal text-field"
                >
                  <span class="tabular">{{ groupAttention(group) }}</span>
                </span>
              </span>
              <span
                class="transition-transform"
                :class="isOpen(group) ? 'rotate-180' : ''"
              >
                <Icon
                  name="chevronDown"
                  size="h-3.5 w-3.5"
                />
              </span>
            </button>
            <h2
              v-else-if="group.label"
              class="px-3 py-1 text-xs font-semibold uppercase tracking-wide text-blue-200"
            >
              {{ group.label }}
            </h2>
            <div
              v-show="isOpen(group)"
              :id="`nav-group-${group.id}`"
              class="space-y-0.5"
            >
              <router-link
                v-for="item in group.items"
                :key="item.to"
                :to="navTarget(item)"
                :aria-current="isActive(item) ? 'page' : undefined"
                class="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-1 text-sm font-medium"
                :class="
                  isActive(item)
                    ? 'bg-white/15 text-white'
                    : 'text-blue-100 hover:bg-white/10 hover:text-white'
                "
              >
                <Icon :name="item.icon" />
                <span class="flex-1">{{ item.label }}</span>
                <span
                  v-if="badgeFor(item)"
                  class="flex h-5 min-w-5 items-center justify-center rounded-full bg-signal px-1.5 text-xs font-bold text-field"
                  :data-testid="`nav-badge-${item.badge}`"
                >
                  <span class="tabular">{{ badgeFor(item) }}</span>
                </span>
              </router-link>
            </div>
          </div>
        </nav>

        <!-- R10 (WCAG 3.2.6): Ask HR sits in the same place on every page,
             beside Sign out on one row -- the row the 800px budget above
             counts once. -->
        <div class="flex gap-1 px-3 pb-1 pt-2">
          <router-link
            v-if="askHr"
            :to="askHr"
            class="flex min-h-9 flex-1 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm font-medium text-blue-100 hover:bg-white/10 hover:text-white"
          >
            <Icon name="askHr" />
            Ask HR
          </router-link>
          <button
            class="flex min-h-9 flex-1 cursor-pointer items-center gap-2 rounded-lg px-3 text-sm font-medium text-blue-200 hover:bg-white/10 hover:text-white"
            @click="signOut"
          >
            <Icon name="signOut" />
            Sign out
          </button>
        </div>

        <!-- AGPLv3 section 5(c): this credit is part of the license notice
             and stays intact in any copy or modified version, the same as
             the notice in index.html and LICENSE. -->
        <a
          href="https://nitcoinc.ai"
          target="_blank"
          rel="noopener noreferrer"
          class="block px-5 pb-3 pt-1 text-xs text-blue-200/70 hover:text-blue-100"
        >
          Powered by Nitco Inc
        </a>
      </aside>
    </div>

    <!-- min-h-screen on the phone: the outer wrapper is only a flex
         container at `lg:`, so below that this column has no height for
         `main` to fill, and a page shorter than the viewport leaves its
         action bar floating mid-screen. -->
    <div class="flex min-h-screen min-w-0 flex-1 flex-col lg:min-h-0">
      <!-- Phone/tablet app bar. The desktop identity and notification
           count live in the sidebar instead, so this is hidden there. -->
      <header
        class="sticky top-0 z-10 flex items-center justify-between bg-field px-4 py-2.5 lg:hidden"
      >
        <router-link
          to="/"
          class="flex min-h-11 cursor-pointer items-center gap-2"
        >
          <span
            class="flex h-7 w-7 items-center justify-center rounded-md bg-signal font-heading text-xs font-bold text-field"
          >
            H
          </span>
          <span class="font-heading text-sm font-semibold text-white">HelixHR</span>
        </router-link>
        <a
          v-if="session.deskUrl"
          :href="session.deskUrl"
          class="ml-auto mr-1 inline-flex min-h-11 cursor-pointer items-center rounded-md px-3 text-xs font-medium text-blue-100 hover:bg-white/10 hover:text-white"
        >
          Open Desk
        </a>
        <router-link
          v-if="!deskOnly"
          to="/notifications"
          class="relative flex h-11 w-11 cursor-pointer items-center justify-center rounded-full text-blue-100 hover:bg-white/10"
          aria-label="Notifications"
        >
          <Icon name="notifications" />
          <span
            v-if="unread > 0"
            class="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-signal px-1 text-[10px] font-bold text-field"
          >
            <span class="tabular">{{ unreadLabel }}</span>
          </span>
        </router-link>
      </header>

      <!-- pb-24 clears the fixed phone tab bar; the sidebar layout has no
           bar to clear, so desktop drops back to normal padding. -->
      <!-- `tabindex="-1"`: the skip link and a page change move focus here
           (R10); it is never a Tab stop of its own. -->
      <main
        id="main"
        ref="mainEl"
        tabindex="-1"
        class="mx-auto w-full max-w-5xl flex-1 px-4 pt-5 pb-[calc(5.5rem+env(safe-area-inset-bottom))] focus:outline-none sm:px-6 lg:pb-10"
      >
        <slot />
      </main>
    </div>

    <!-- Phone/tablet bottom tab bar. `data-toast-clear`: toasts stack above
         it (Toaster.vue). -->
    <nav
      class="fixed inset-x-0 bottom-0 z-10 flex bg-field pb-[env(safe-area-inset-bottom)] lg:hidden"
      aria-label="Main"
      data-toast-clear
    >
      <!-- KTD7: the badge rides on the Approvals tab for a stable approver,
           and on More for a queue-dependent one. Visual only: the count is
           also on the row it belongs to, and the tab keeps its one-word
           name. -->
      <router-link
        v-for="item in primaryItems"
        :key="item.to"
        :to="navTarget(item)"
        :aria-current="isActive(item) ? 'page' : undefined"
        class="flex min-h-[56px] flex-1 cursor-pointer flex-col items-center justify-center gap-1 text-[11px] font-medium"
        :class="isActive(item) ? 'text-signal' : 'text-blue-200'"
      >
        <span class="relative">
          <Icon :name="item.icon" />
          <span
            v-if="item.badge === 'approvals' && badgeFor(item)"
            aria-hidden="true"
            class="absolute -right-3 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-signal px-1 text-[10px] font-bold text-field"
            data-testid="tab-badge-approvals"
          >
            <span class="tabular">{{ badgeFor(item) }}</span>
          </span>
        </span>
        {{ item.label }}
      </router-link>
      <button
        class="flex min-h-[56px] flex-1 cursor-pointer flex-col items-center justify-center gap-1 text-[11px] font-medium"
        :class="moreIsActive ? 'text-signal' : 'text-blue-200'"
        :aria-current="moreIsActive ? 'page' : undefined"
        :aria-expanded="showMore"
        aria-haspopup="dialog"
        @click="showMore = true"
      >
        <span class="relative">
          <Icon name="more" />
          <span
            v-if="moreBadge"
            aria-hidden="true"
            class="absolute -right-3 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-signal px-1 text-[10px] font-bold text-field"
            data-testid="tab-badge-more"
          >
            <span class="tabular">{{ moreBadge }}</span>
          </span>
        </span>
        More
      </button>
    </nav>

    <Dialog
      v-model="showMore"
      :options="{ title: 'More' }"
    >
      <template #body-content>
        <div class="space-y-0.5">
          <!-- R9: same groups as the rail, always open, no collapse control. -->
          <section
            v-for="group in moreGroups"
            :key="group.id"
            :aria-labelledby="group.label ? `more-group-${group.id}` : undefined"
            class="space-y-0.5"
            :class="group.label || group.id === 'bottom' ? 'pt-2' : ''"
          >
            <h2
              v-if="group.label"
              :id="`more-group-${group.id}`"
              class="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-ink-gray-5"
            >
              {{ group.label }}
            </h2>
            <router-link
              v-for="item in group.items"
              :key="item.to"
              :to="navTarget(item)"
              :aria-current="isActive(item) ? 'page' : undefined"
              class="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium"
              :class="
                isActive(item)
                  ? 'bg-surface-gray-2 text-ink-gray-9'
                  : 'text-ink-gray-7 hover:bg-surface-gray-2'
              "
              @click="closeMore"
            >
              <Icon :name="item.icon" />
              <span class="flex-1">{{ item.label }}</span>
              <span
                v-if="badgeFor(item)"
                class="flex h-5 min-w-5 items-center justify-center rounded-full bg-signal px-1.5 text-xs font-bold text-field"
              >
                <span class="tabular">{{ badgeFor(item) }}</span>
              </span>
              <Icon
                v-else
                name="chevronRight"
                size="h-4 w-4"
              />
            </router-link>
          </section>
          <!-- R10: the same Ask HR as the rail's footer, in the same place
               on every page's More sheet. -->
          <router-link
            v-if="askHr"
            :to="askHr"
            class="mt-2 flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2"
            @click="closeMore"
          >
            <Icon name="askHr" />
            <span class="flex-1">Ask HR</span>
          </router-link>
          <button
            class="flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium text-ink-gray-6 hover:bg-surface-gray-2"
            @click="signOut"
          >
            <Icon name="signOut" />
            Sign out
          </button>
        </div>
        <a
          href="https://nitcoinc.ai"
          target="_blank"
          rel="noopener noreferrer"
          class="mt-3 block px-3 text-center text-xs text-ink-gray-4"
        >
          Powered by Nitco Inc
        </a>
      </template>
    </Dialog>

    <Toaster v-if="toasterWanted" />
  </div>
</template>
