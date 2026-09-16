<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { createResource } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'
import CategoriesSection from '@/components/settings/CategoriesSection.vue'
import TemplatesSection from '@/components/settings/TemplatesSection.vue'
import LeaveTypesSection from '@/components/settings/LeaveTypesSection.vue'
import HolidayListsSection from '@/components/settings/HolidayListsSection.vue'
import ShiftTypesSection from '@/components/settings/ShiftTypesSection.vue'

// P5-U14: `get_portal_config` is HR-only (`_is_hr()`, the same predicate
// `can_configure` mirrors in the bootstrap). A non-HR caller hitting this
// page directly gets AsyncState's 'forbidden' region below -- the server's
// own gate, not a client-side redirect.
const props = defineProps({
  section: { type: String, default: 'categories' },
})
const router = useRouter()

const config = createResource({
  url: 'helixhr.api.get_portal_config',
  auto: true,
})

const SECTIONS = [
  { key: 'categories', label: 'Categories' },
  { key: 'templates', label: 'Message text' },
  { key: 'leave-types', label: 'Leave types' },
  { key: 'holiday-lists', label: 'Holiday lists' },
  { key: 'shift-types', label: 'Shift types' },
]

const activeSection = computed(() => props.section || 'categories')

function selectSection(key) {
  router.push(key === 'categories' ? '/settings' : `/settings/${key}`)
}

function reload() {
  config.reload()
}
</script>

<template>
  <div>
    <PageHeader
      title="Settings"
      subtitle="Request categories, message text, and the day-to-day HRMS masters -- without Desk."
    />

    <AsyncState
      section="settings"
      :resource="config"
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
        <TemplatesSection
          v-else-if="activeSection === 'templates'"
          :templates="config.data?.templates || []"
          :template-tokens="config.data?.template_tokens || {}"
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
      </div>
    </AsyncState>
  </div>
</template>
