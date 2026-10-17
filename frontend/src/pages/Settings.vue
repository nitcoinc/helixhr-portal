<script setup>
import { computed } from 'vue'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import TabBar from '@/components/TabBar.vue'
import CategoriesSection from '@/components/settings/CategoriesSection.vue'
import LeaveTypesSection from '@/components/settings/LeaveTypesSection.vue'
import HolidayListsSection from '@/components/settings/HolidayListsSection.vue'
import ShiftTypesSection from '@/components/settings/ShiftTypesSection.vue'
import LeaveRulesSection from '@/components/settings/LeaveRulesSection.vue'
import ReportAccessSection from '@/components/settings/ReportAccessSection.vue'
import PeopleAccessSection from '@/components/settings/PeopleAccessSection.vue'
import ApproverCleanupSection from '@/components/settings/ApproverCleanupSection.vue'
import { session } from '@/lib/session'

// P5-U14: `get_portal_config` is HR-only (`_is_hr()`, the same predicate
// `can_configure` mirrors in the bootstrap). A non-HR caller hitting this
// page directly gets AsyncState's 'forbidden' region below -- the server's
// own gate, not a client-side redirect.
const props = defineProps({
  section: { type: String, default: '' },
})

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
  // Plan 2026-10-06-001 U2: the backdated leave rule, owned by the same
  // audience as Leave types (`can_configure`).
  { key: 'leave-rules', label: 'Leave rules' },
  // Plan 2026-10-04-004 U5: celebrations moved to the Email templates
  // page's own group; /settings/celebrations redirects there.
  { key: 'report-access', label: 'Report access' },
  // Plan 2026-10-15-001 U6: People & access replaces Portal roles and
  // Company access; their old URLs redirect here (router.js).
  { key: 'people-access', label: 'People & access' },
  // Plan 2026-10-07-001 U4: the "approvers follow Reports to" cleanup.
  { key: 'approvers', label: 'Approvers' },
]
// Plan 2026-10-06-001 U3 (KTD4, R5): the tab list derives from the two
// flags. HR sees the HR sections, Portal Admin sees the admin sections, and
// someone holding both roles (System Manager is both) sees both. A caller
// with neither still receives the full list, so what they meet is the
// server's refusal, never a client-side redirect.
const HR_SECTIONS = ['categories', 'leave-types', 'holiday-lists', 'shift-types', 'leave-rules']
const PORTAL_SECTIONS = ['report-access', 'people-access', 'approvers']
const SECTIONS =
  !session.canConfigure && !session.canAdminPortal
    ? ALL_SECTIONS
    : ALL_SECTIONS.filter(
        (tab) =>
          (session.canConfigure && HR_SECTIONS.includes(tab.key)) ||
          (session.canAdminPortal && PORTAL_SECTIONS.includes(tab.key)),
      )
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

// Route-segment sections, so the bar is links (`nav` mode): a reload, a
// middle-click and the back button all land on the section.
const sectionTabs = SECTIONS.map((tab) => ({
  ...tab,
  to: tab.key === DEFAULT_SECTION ? '/settings' : `/settings/${tab.key}`,
  testid: `settings-tab-${tab.key}`,
}))

function reload() {
  config.reload()
}
</script>

<template>
  <div>
    <PageHeader
      title="Settings"
      :subtitle="portalOnly ? 'Who can run reports, who has which access, and who approves.' : 'Request categories, leave types, holiday lists, shift types and leave rules.'"
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
          class="inline-flex min-h-11 items-center rounded px-3 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-3"
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
        <!-- One scrolling line at every width (HR-09): five sections used to
             wrap onto two rows at 390px. -->
        <TabBar
          nav
          :tabs="sectionTabs"
          :model-value="activeSection"
          label="Settings sections"
        />

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
        <LeaveRulesSection v-else-if="activeSection === 'leave-rules'" />
        <ReportAccessSection v-else-if="activeSection === 'report-access'" />
        <PeopleAccessSection v-else-if="activeSection === 'people-access'" />
        <ApproverCleanupSection v-else-if="activeSection === 'approvers'" />
      </div>
    </AsyncState>
  </div>
</template>
