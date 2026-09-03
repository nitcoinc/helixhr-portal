<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Dialog } from 'frappe-ui'
import Icon from '@/components/Icon.vue'
import { session, signOut } from '@/lib/session'
import { unreadCount, watchUnread, unwatchUnread } from '@/lib/unread'

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

const isManager = computed(() => (session.reportCount || 0) > 0)
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

function closeMore() {
  showMore.value = false
}

onMounted(watchUnread)
onUnmounted(unwatchUnread)
</script>

<template>
  <div class="min-h-screen bg-surface-gray-1 lg:flex">
    <!-- Desktop side nav (>=1024px). Hidden on phone/tablet, where the
         bottom tab bar below takes over. -->
    <!-- The white column is the outer div so it stretches to the full
         page height on a long page; the sticky inner aside is only as
         tall as the viewport. Sticky alone left a bare grey strip below
         the nav once content scrolled past one screen. -->
    <div class="hidden shrink-0 border-r border-outline-gray-2 bg-surface-white lg:block">
      <aside class="sticky top-0 flex h-screen w-64 flex-col">
        <div class="flex items-center gap-2 px-5 py-5">
          <span
            class="flex h-8 w-8 items-center justify-center rounded-md bg-blue-700 font-heading text-sm font-semibold text-white"
          >
            H
          </span>
          <span class="font-heading text-base font-semibold text-ink-gray-9">HelixHR</span>
        </div>

        <router-link
          to="/profile"
          class="mx-3 flex cursor-pointer items-center gap-3 rounded-lg px-2 py-2 hover:bg-surface-gray-2"
        >
          <span
            class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-50 font-medium text-blue-700"
          >
            {{ initials || '—' }}
          </span>
          <span class="min-w-0">
            <span class="block truncate text-sm font-medium text-ink-gray-8">
              {{ employeeName || 'Loading…' }}
            </span>
            <span
              v-if="employeeRole"
              class="block truncate text-xs text-ink-gray-5"
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
                ? 'bg-blue-50 text-blue-700'
                : 'text-ink-gray-7 hover:bg-surface-gray-2 hover:text-ink-gray-9'
            "
          >
            <Icon :name="item.icon" />
            <span class="flex-1">{{ item.label }}</span>
            <span
              v-if="item.badge && unreadCount.data > 0"
              class="flex h-5 min-w-5 items-center justify-center rounded-full bg-blue-700 px-1.5 text-xs font-medium text-white"
            >
              <span class="tabular">{{ unreadCount.data > 9 ? '9+' : unreadCount.data }}</span>
            </span>
          </router-link>
        </nav>

        <button
          class="m-3 flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-ink-gray-6 hover:bg-surface-gray-2 hover:text-ink-gray-9"
          @click="signOut"
        >
          <Icon name="signOut" />
          Sign out
        </button>
      </aside>
    </div>

    <div class="flex min-w-0 flex-1 flex-col">
      <!-- Phone/tablet app bar. The desktop identity and notification
           count live in the sidebar instead, so this is hidden there. -->
      <header
        class="sticky top-0 z-10 flex items-center justify-between border-b border-outline-gray-2 bg-surface-white px-4 py-2.5 lg:hidden"
      >
        <router-link
          to="/"
          class="flex min-h-11 cursor-pointer items-center gap-2"
        >
          <span
            class="flex h-7 w-7 items-center justify-center rounded-md bg-blue-700 font-heading text-xs font-semibold text-white"
          >
            H
          </span>
          <span class="font-heading text-sm font-semibold text-ink-gray-9">HelixHR</span>
        </router-link>
        <router-link
          to="/notifications"
          class="relative flex h-11 w-11 cursor-pointer items-center justify-center rounded-full text-ink-gray-6 hover:bg-surface-gray-2"
          aria-label="Notifications"
        >
          <Icon name="notifications" />
          <span
            v-if="unreadCount.data > 0"
            class="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-700 px-1 text-[10px] font-medium text-white"
          >
            <span class="tabular">{{ unreadCount.data > 9 ? '9+' : unreadCount.data }}</span>
          </span>
        </router-link>
      </header>

      <!-- pb-24 clears the fixed phone tab bar; the sidebar layout has no
           bar to clear, so desktop drops back to normal padding. -->
      <main class="mx-auto w-full max-w-5xl flex-1 px-4 pb-24 pt-5 sm:px-6 lg:pb-10">
        <slot />
      </main>
    </div>

    <!-- Phone/tablet bottom tab bar. -->
    <nav
      class="fixed inset-x-0 bottom-0 z-10 flex border-t border-outline-gray-2 bg-surface-white lg:hidden"
      aria-label="Main"
    >
      <router-link
        v-for="item in primaryItems"
        :key="item.to"
        :to="item.to"
        :aria-current="isActive(item) ? 'page' : undefined"
        class="flex min-h-[56px] flex-1 cursor-pointer flex-col items-center justify-center gap-1 text-[11px] font-medium"
        :class="isActive(item) ? 'text-blue-700' : 'text-ink-gray-6'"
      >
        <Icon :name="item.icon" />
        {{ item.label }}
      </router-link>
      <button
        class="flex min-h-[56px] flex-1 cursor-pointer flex-col items-center justify-center gap-1 text-[11px] font-medium text-ink-gray-6"
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
            class="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2"
            @click="closeMore"
          >
            <Icon :name="item.icon" />
            <span class="flex-1">{{ item.label }}</span>
            <span
              v-if="item.badge && unreadCount.data > 0"
              class="flex h-5 min-w-5 items-center justify-center rounded-full bg-blue-700 px-1.5 text-xs font-medium text-white"
            >
              <span class="tabular">{{ unreadCount.data > 9 ? '9+' : unreadCount.data }}</span>
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
