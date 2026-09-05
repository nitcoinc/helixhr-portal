<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Dialog } from 'frappe-ui'
import Icon from '@/components/Icon.vue'
import { session, signOut } from '@/lib/session'
import { unreadCount, watchUnread, unwatchUnread } from '@/lib/unread'
import { watchDialogs, unwatchDialogs } from '@/lib/dialogA11y'

// `primary` items are the four that fit the phone tab bar alongside
// "More" (design system: max 5 tab items). Everything else lives in the
// desktop sidebar and, on a phone, behind More.
const NAV = [
  { label: 'Home', to: '/', icon: 'home', primary: true },
  { label: 'Leave', to: '/leave', icon: 'leave', primary: true },
  { label: 'Timesheet', to: '/timesheet', icon: 'timesheet', primary: true },
  { label: 'Requests', to: '/requests', icon: 'requests', primary: true },
  { label: 'Attendance', to: '/attendance', icon: 'attendance' },
  { label: 'Documents', to: '/documents', icon: 'documents' },
  { label: 'Approvals', to: '/approvals', icon: 'approvals', managerOnly: true },
  { label: 'Notifications', to: '/notifications', icon: 'notifications', badge: true },
  { label: 'Profile', to: '/profile', icon: 'profile' },
]

const route = useRoute()
const showMore = ref(false)

// The bootstrap already answered "how many unread" (P2-KTD7), and the shared
// poller's first fetch takes a round trip to say the same thing. Preferring
// the poll once it has an answer and falling back to the boot value means the
// badge is right on the first painted frame instead of appearing a beat later
// -- one fewer thing moving on a cold load, and one fewer reason for the
// count to look stale after an action (P2-U3 step 6).
const unread = computed(() => unreadCount.data ?? session.unread ?? 0)
const unreadLabel = computed(() => (unread.value > 9 ? '9+' : String(unread.value)))

// P2-U4 / P2-R11: the bootstrap's own answer, not a second rule derived from
// the report count. A leave approver need not be anybody's manager, and a
// manager whose only pending work is a timesheet has a decision to make with
// no direct-report leave in sight -- both had no Approvals item at all while
// this read `reportCount`.
const isManager = computed(() => session.canApprove)
const navItems = computed(() => NAV.filter((item) => !item.managerOnly || isManager.value))
const primaryItems = computed(() => navItems.value.filter((item) => item.primary))
const moreItems = computed(() => navItems.value.filter((item) => !item.primary))

const employeeName = computed(() => session.employee?.employee_name || '')
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
  watchUnread()
  // P2-U9: names frappe-ui's unlabelled dialog close button, once for the
  // whole application. See src/lib/dialogA11y.js.
  watchDialogs()
})
onUnmounted(() => {
  unwatchUnread()
  unwatchDialogs()
})
</script>

<template>
  <div class="min-h-screen bg-surface-gray-1 lg:flex">
    <!-- Desktop side nav (>=1024px). Hidden on phone/tablet, where the
         bottom tab bar below takes over. -->
    <!-- The white column is the outer div so it stretches to the full
         page height on a long page; the sticky inner aside is only as
         tall as the viewport. Sticky alone left a bare grey strip below
         the nav once content scrolled past one screen. -->
    <div class="hidden shrink-0 bg-field lg:block">
      <aside class="sticky top-0 flex h-screen w-64 flex-col">
        <div class="flex items-center gap-2 px-5 py-5">
          <span
            class="flex h-8 w-8 items-center justify-center rounded-md bg-signal font-heading text-sm font-bold text-field"
          >
            H
          </span>
          <span class="font-heading text-base font-semibold text-white">HelixHR</span>
        </div>

        <router-link
          to="/profile"
          class="mx-3 flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2 hover:bg-white/10"
        >
          <span
            class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/15 font-medium text-white"
          >
            {{ initials || '—' }}
          </span>
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
          class="mt-4 flex-1 space-y-0.5 overflow-y-auto px-3"
          aria-label="Main"
        >
          <router-link
            v-for="item in navItems"
            :key="item.to"
            :to="item.to"
            :aria-current="isActive(item) ? 'page' : undefined"
            class="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium"
            :class="
              isActive(item)
                ? 'bg-white/15 text-white'
                : 'text-blue-100 hover:bg-white/10 hover:text-white'
            "
          >
            <Icon :name="item.icon" />
            <span class="flex-1">{{ item.label }}</span>
            <span
              v-if="item.badge && unread > 0"
              class="flex h-5 min-w-5 items-center justify-center rounded-full bg-signal px-1.5 text-xs font-bold text-field"
            >
              <span class="tabular">{{ unreadLabel }}</span>
            </span>
          </router-link>
        </nav>

        <button
          class="m-3 flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-blue-200 hover:bg-white/10 hover:text-white"
          @click="signOut"
        >
          <Icon name="signOut" />
          Sign out
        </button>
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
        <router-link
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
      <main
        class="mx-auto w-full max-w-5xl flex-1 px-4 pt-5 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:px-6 lg:pb-10"
      >
        <slot />
      </main>
    </div>

    <!-- Phone/tablet bottom tab bar. -->
    <nav
      class="fixed inset-x-0 bottom-0 z-10 flex bg-field pb-[env(safe-area-inset-bottom)] lg:hidden"
      aria-label="Main"
    >
      <router-link
        v-for="item in primaryItems"
        :key="item.to"
        :to="item.to"
        :aria-current="isActive(item) ? 'page' : undefined"
        class="flex min-h-[56px] flex-1 cursor-pointer flex-col items-center justify-center gap-1 text-[11px] font-medium"
        :class="isActive(item) ? 'text-signal' : 'text-blue-200'"
      >
        <Icon :name="item.icon" />
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
        <Icon name="more" />
        More
      </button>
    </nav>

    <Dialog
      v-model="showMore"
      :options="{ title: 'More' }"
    >
      <template #body-content>
        <div class="space-y-0.5">
          <router-link
            v-for="item in moreItems"
            :key="item.to"
            :to="item.to"
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
              v-if="item.badge && unread > 0"
              class="flex h-5 min-w-5 items-center justify-center rounded-full bg-signal px-1.5 text-xs font-bold text-field"
            >
              <span class="tabular">{{ unreadLabel }}</span>
            </span>
            <Icon
              v-else
              name="chevronRight"
              size="h-4 w-4"
            />
          </router-link>
          <button
            class="flex min-h-11 w-full cursor-pointer items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium text-ink-gray-6 hover:bg-surface-gray-2"
            @click="signOut"
          >
            <Icon name="signOut" />
            Sign out
          </button>
        </div>
      </template>
    </Dialog>
  </div>
</template>
