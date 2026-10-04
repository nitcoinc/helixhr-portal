<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import CategoriesSection from '@/components/settings/CategoriesSection.vue'
import LeaveTypesSection from '@/components/settings/LeaveTypesSection.vue'
import HolidayListsSection from '@/components/settings/HolidayListsSection.vue'
import ShiftTypesSection from '@/components/settings/ShiftTypesSection.vue'
import CelebrationsSection from '@/components/settings/CelebrationsSection.vue'
import ReportAccessSection from '@/components/settings/ReportAccessSection.vue'
import PortalRolesSection from '@/components/settings/PortalRolesSection.vue'
import { session } from '@/lib/session'

// P5-U14: `get_portal_config` is HR-only (`_is_hr()`, the same predicate
// `can_configure` mirrors in the bootstrap). A non-HR caller hitting this
// page directly gets AsyncState's 'forbidden' region below -- the server's
// own gate, not a client-side redirect.
const props = defineProps({
  section: { type: String, default: '' },
})
const router = useRouter()

// A Portal Admin who is not HR gets only the two sections whose endpoints
// accept it, and never calls `get_portal_config` (HR-only). Anyone else still
// goes through that call, so a direct hit keeps the server's refusal.
const portalOnly = !session.canConfigure && session.canAdminPortal

const config = createResource({
  url: 'helixhr.api.get_portal_config',
  auto: !portalOnly,
})

const ALL_SECTIONS = [
  { key: 'categories', label: 'Categories' },
  { key: 'leave-types', label: 'Leave types' },
  { key: 'holiday-lists', label: 'Holiday lists' },
  { key: 'shift-types', label: 'Shift types' },
  { key: 'celebrations', label: 'Celebrations' },
  { key: 'report-access', label: 'Report access' },
  { key: 'portal-roles', label: 'Portal roles' },
]
const PORTAL_SECTIONS = ['report-access', 'portal-roles']
const SECTIONS = portalOnly ? ALL_SECTIONS.filter((tab) => PORTAL_SECTIONS.includes(tab.key)) : ALL_SECTIONS
const DEFAULT_SECTION = SECTIONS[0].key

const activeSection = computed(() =>
  SECTIONS.some((tab) => tab.key === props.section) ? props.section : DEFAULT_SECTION,
)

// P8-U6: `get_portal_config`'s `desk_urls` keys match its own response
// shape (`leave_types`, `holiday_lists`, `shift_types`), snake_case like
// every other key in that payload -- SECTIONS' keys are kebab-case for the
// URL segment they route to, so the two are reconciled here rather than
// making one side match the other's convention for a reason that belongs
// to it alone.
const activeDeskUrl = computed(() => {
  const key = activeSection.value.replace(/-/g, '_')
  return config.data?.desk_urls?.[key] || null
})

function selectSection(key) {
  router.push(key === DEFAULT_SECTION ? '/settings' : `/settings/${key}`)
}

function reload() {
  config.reload()
}
</script>

<template>
  <div>
    <PageHeader
      title="Settings"
      :subtitle="portalOnly ? 'Report access and portal roles -- without Desk.' : 'Request categories and the day-to-day HRMS masters -- without Desk.'"
    >
      <template #actions>
        <!-- P8-U6: the server's own gate (`_can_open_desk`), not merely
             hidden client-side -- a caller who cannot reach Desk never
             receives a URL to it at all. -->
        <a
          v-if="activeDeskUrl"
          :href="activeDeskUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex min-h-11 items-center rounded-lg border border-outline-gray-2 px-3 text-sm font-medium text-ink-gray-7 hover:bg-surface-gray-2"
          data-testid="settings-desk-link"
        >
          Open in Desk
        </a>
      </template>
    </PageHeader>

    <AsyncState
      section="settings"
      :resource="portalOnly ? null : config"
      :empty="false"
      skeleton="block"
      skeleton-height="h-96"
    >
      <div class="space-y-5">
        <nav
          class="flex flex-wrap gap-1 border-b border-outline-gray-1"
          aria-label="Settings sections"
        >
          <button
            v-for="tab in SECTIONS"
            :key="tab.key"
            type="button"
            class="min-h-11 cursor-pointer rounded-t-lg px-3 py-2 text-sm font-medium"
            :aria-current="activeSection === tab.key ? 'page' : undefined"
            :class="
              activeSection === tab.key
                ? 'border-b-2 border-signal text-ink-gray-9'
                : 'text-ink-gray-6 hover:text-ink-gray-9'
            "
            :data-testid="`settings-tab-${tab.key}`"
            @click="selectSection(tab.key)"
          >
            {{ tab.label }}
          </button>
        </nav>

        <CategoriesSection
          v-if="activeSection === 'categories'"
          :categories="config.data?.categories || []"
          @saved="reload"
        />
        <LeaveTypesSection
          v-else-if="activeSection === 'leave-types'"
          :leave-types="config.data?.leave_types || []"
          @saved="reload"
        />
        <HolidayListsSection
          v-else-if="activeSection === 'holiday-lists'"
          :holiday-lists="config.data?.holiday_lists || []"
          @saved="reload"
        />
        <ShiftTypesSection
          v-else-if="activeSection === 'shift-types'"
          :shift-types="config.data?.shift_types || []"
          @saved="reload"
        />
        <CelebrationsSection
          v-else-if="activeSection === 'celebrations'"
          :celebrations="config.data?.celebrations || {}"
          :template-tokens="config.data?.celebration_template_tokens || []"
          @saved="reload"
        />
        <ReportAccessSection v-else-if="activeSection === 'report-access'" />
        <PortalRolesSection v-else-if="activeSection === 'portal-roles'" />
      </div>
    </AsyncState>
  </div>
</template>
