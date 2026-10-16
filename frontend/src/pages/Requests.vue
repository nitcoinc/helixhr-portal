<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Button } from 'frappe-ui'
import PageHeader from '@/components/PageHeader.vue'
import TabBar from '@/components/TabBar.vue'
import MyRequests from '@/components/requests/MyRequests.vue'
import RequestWorkspace from '@/components/requests/RequestWorkspace.vue'
import { session } from '@/lib/session'

// P2-U8 / KTD5. `/requests` and `/requests/:name` are the same component: the
// selected record is a route parameter, so refresh and browser Back land on
// the same request, and the phone view and the desktop panel are two shapes
// of one state rather than two screens.
//
// Plan 2026-10-17-001 U8 (R21, KTD14): the two workspaces are components --
// `MyRequests` (the employee's own) and `RequestWorkspace` (the Team inbox).
// The page keeps what both share: the header, the tab bar and the URL.
defineProps({
  name: { type: String, default: '' },
})

const route = useRoute()
const router = useRouter()

const WORK_TABS = [
  { key: 'mine', label: 'My requests' },
  { key: 'work', label: 'Team inbox' },
]

const tab = computed(() =>
  session.canHandleRequests && route.query.tab === 'work' ? 'work' : 'mine',
)

function showTab(name) {
  router.push({
    name: route.name,
    params: route.params,
    query: name === 'work' ? { tab: 'work' } : {},
  })
}

// The inbox reports its title and badge count once it has loaded; the last
// answer stays on the tab after switching back to My requests.
const inbox = ref({ title: 'Team inbox', count: 0 })
const workTabs = computed(() =>
  WORK_TABS.map((item) => ({
    ...item,
    count: item.key === 'work' && inbox.value.count > 0 ? inbox.value.count : null,
  })),
)

const mine = ref(null)
function newRequest() {
  mine.value?.newRequest()
}
</script>

<template>
  <div>
    <PageHeader :title="tab === 'work' ? inbox.title : 'Requests'">
      <template #actions>
        <Button
          v-if="tab !== 'work'"
          variant="solid"
          theme="blue"
          @click="newRequest"
        >
          New request
        </Button>
      </template>
    </PageHeader>

    <!-- Plan 2026-10-04-002 R6/R9: two workspace tabs for a holder of a
         routed role; a user with neither role gets no tab bar at all. A
         two-item tab bar, in the URL like the category chips. -->
    <TabBar
      v-if="session.canHandleRequests"
      class="mb-4"
      :tabs="workTabs"
      :model-value="tab"
      label="Requests views"
      data-testid="requests-tabs"
      @update:model-value="showTab"
    />

    <RequestWorkspace
      v-if="tab === 'work'"
      :name="name"
      @summary="inbox = $event"
    />
    <MyRequests
      v-else
      ref="mine"
      :name="name"
    />
  </div>
</template>
