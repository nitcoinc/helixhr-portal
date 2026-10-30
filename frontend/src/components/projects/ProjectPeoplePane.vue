<script setup>
// Plan 2026-10-31-001 U3 / U6 (R3, R8). A project's people: search, show
// more, remove, "Add people" (ProjectMemberPicker) and the "Open to
// everyone" switch. The parent owns `get_project`; this pane hands back what
// changed as `update:members` / `update:openToAll`. A write that lands after
// the route moved to another project is dropped, so it never lands on the
// wrong one.
import { computed, ref, toRef, watch } from 'vue'
import { createResource, Button, Dialog } from 'frappe-ui'
import Avatar from '@/components/Avatar.vue'
import ProjectMemberPicker from '@/components/ProjectMemberPicker.vue'
import SearchField from '@/components/SearchField.vue'
import { useProjectPane } from '@/lib/useProjectPane'

const props = defineProps({
  project: { type: String, required: true },
  /** `get_project().members`. */
  members: { type: Array, default: () => [] },
  /** `get_project().departments`, for the picker's filter. */
  departments: { type: Array, default: () => [] },
  openToAll: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
  /** The project's company, or "this company". */
  companyName: { type: String, default: 'this company' },
})
const emit = defineEmits(['update:members', 'update:openToAll'])

const pane = useProjectPane(toRef(props, 'members'), 'employee_name')
const { query, shown, more, showMore } = pane
const memberEmployeeIds = computed(() => props.members.map((m) => m.employee).filter(Boolean))

// --- members -------------------------------------------------------------------
//
// U6: adding goes through ProjectMemberPicker (`add_project_members`, which
// appends). Removing still replaces the list through `set_project_members`.

const memberError = ref('')
const pickerOpen = ref(false)
const setMembers = createResource({ url: 'helixhr.api.set_project_members', method: 'POST' })

function onMembersAdded(response) {
  memberError.value = ''
  if (response?.members) emit('update:members', response.members)
}

async function removeMember(member) {
  if (!member.employee) return
  memberError.value = ''
  const project = props.project
  const employees = memberEmployeeIds.value.filter((id) => id !== member.employee)
  try {
    await setMembers.submit({ project, employees })
    if (props.project !== project) return
    emit(
      'update:members',
      props.members.filter((row) => row.employee !== member.employee),
    )
  } catch (error) {
    if (props.project !== project) return
    memberError.value = error?.messages?.[0] || 'Could not remove that person. Please try again.'
  }
}

// --- "Open to everyone" (U6, R8) ----------------------------------------------
//
// Either direction is confirmed first, naming its effect. While the write is
// in flight the switch shows the new state as pending; a refusal puts it
// back and says why.

const confirmOpenToAll = ref(false)
const openTarget = ref(false)
const openPending = ref(null)
const openError = ref('')
const setOpenToAll = createResource({ url: 'helixhr.api.set_project_open_to_all', method: 'POST' })
const switchOn = computed(() => openPending.value ?? props.openToAll)

function askOpenToAll() {
  if (openPending.value !== null) return
  openError.value = ''
  openTarget.value = !props.openToAll
  confirmOpenToAll.value = true
}

async function applyOpenToAll() {
  const project = props.project
  const target = openTarget.value
  confirmOpenToAll.value = false
  openPending.value = target
  try {
    const response = await setOpenToAll.submit({ project, value: target ? 1 : 0 })
    if (props.project !== project) return
    emit('update:openToAll', !!response?.open_to_all)
  } catch (error) {
    if (props.project !== project) return
    openError.value =
      error?.messages?.[0] || `Could not change that. The project is still ${target ? 'members only' : 'open to everyone'}.`
  } finally {
    // Another project's switch is not this write's to clear.
    if (props.project === project) openPending.value = null
  }
}

watch(
  () => props.project,
  () => {
    pane.reset()
    memberError.value = ''
    pickerOpen.value = false
    confirmOpenToAll.value = false
    openPending.value = null
    openError.value = ''
  },
)
</script>

<template>
  <section
    class="surface-card elev-1 min-w-0 p-4"
    data-testid="project-people-pane"
  >
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2
        id="project-members-heading"
        class="font-heading text-base font-semibold text-ink-gray-9"
      >
        People <span class="tabular text-sm font-normal text-ink-gray-5">{{ members.length }}</span>
      </h2>
      <button
        v-if="canEdit"
        type="button"
        role="switch"
        class="flex min-h-11 cursor-pointer items-center gap-3 text-left text-sm font-medium text-ink-gray-9 aria-disabled:cursor-progress"
        :aria-checked="switchOn ? 'true' : 'false'"
        :aria-disabled="openPending !== null ? 'true' : undefined"
        :aria-busy="openPending !== null ? 'true' : undefined"
        data-testid="project-open-toggle"
        @click="askOpenToAll"
      >
        <span
          class="flex h-6 w-10 shrink-0 items-center rounded-full p-0.5 transition-colors duration-200"
          :class="[
            switchOn ? 'justify-end bg-heading' : 'justify-start bg-surface-gray-3',
            openPending !== null ? 'opacity-60' : '',
          ]"
          aria-hidden="true"
        >
          <span class="h-5 w-5 rounded-full bg-white" />
        </span>
        <span class="break-words">Open to everyone in {{ companyName }}</span>
        <span
          v-if="openPending !== null"
          class="text-xs font-normal text-ink-gray-5"
        >Saving…</span>
      </button>
    </div>

    <p
      v-if="openError"
      class="surface-alert mt-3 p-3 text-sm"
      role="alert"
    >
      {{ openError }}
    </p>
    <p
      v-if="openToAll"
      class="mt-2 text-sm text-ink-gray-7"
      data-testid="project-open-note"
    >
      Everyone active in {{ companyName }} can book time here. People below were added by name.
    </p>

    <div
      v-if="members.length"
      class="mt-3"
    >
      <SearchField
        v-model="query"
        label="Search people"
        placeholder="Name"
        maxlength="140"
      />
    </div>
    <p
      class="mt-2 text-xs text-ink-gray-5"
      aria-live="polite"
    >
      {{ pane.countLine(members.length === 1 ? 'person' : 'people') }}
    </p>

    <ul
      v-if="shown.length"
      class="divide-y divide-outline-gray-1"
    >
      <li
        v-for="member in shown"
        :key="member.employee || member.employee_name"
        class="flex items-center gap-3 py-2"
        data-testid="project-member"
      >
        <Avatar
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-green-2 text-xs font-bold text-ink-green-3"
          :photo-url="member.photo_url"
          :initials="member.initials"
          :size="32"
        />
        <span class="min-w-0 flex-1 break-words text-sm font-medium text-ink-gray-9">
          {{ member.employee_name }}
        </span>
        <Button
          v-if="member.employee && canEdit"
          variant="ghost"
          :aria-label="`Remove ${member.employee_name}`"
          :disabled="setMembers.loading"
          @click="removeMember(member)"
        >
          Remove
        </Button>
      </li>
    </ul>
    <p
      v-else
      class="text-sm text-ink-gray-6"
    >
      {{
        members.length
          ? `Nobody here matches “${query.trim()}”.`
          : openToAll
            ? 'Nobody is listed by name.'
            : 'Nobody is assigned to this project yet.'
      }}
    </p>

    <div
      v-if="more > 0"
      class="mt-2 text-center"
    >
      <Button
        variant="ghost"
        @click="showMore"
      >
        Show {{ more }} more
      </Button>
    </div>

    <p
      v-if="memberError"
      class="surface-alert mt-3 p-3 text-sm"
      role="alert"
    >
      {{ memberError }}
    </p>

    <Button
      v-if="canEdit"
      class="mt-4"
      variant="outline"
      @click="pickerOpen = true"
    >
      Add people
    </Button>

    <ProjectMemberPicker
      v-if="canEdit"
      v-model="pickerOpen"
      :project="project"
      :departments="departments"
      :member-ids="memberEmployeeIds"
      @added="onMembersAdded"
    />

    <Dialog
      v-model="confirmOpenToAll"
      :options="{ title: openTarget ? 'Open to everyone?' : 'Only listed members?', size: 'sm' }"
    >
      <template #body-content>
        <p
          class="text-sm text-ink-gray-7"
          data-testid="project-open-confirm"
        >
          {{
            openTarget
              ? `Every active employee in ${companyName} can book time on this project.`
              : 'Only listed members can book. Saved timesheet weeks that still use this project will need that row removed.'
          }}
        </p>
        <div class="mt-4 flex flex-wrap items-center gap-2">
          <Button
            variant="solid"
            theme="blue"
            @click="applyOpenToAll"
          >
            {{ openTarget ? 'Open to everyone' : 'Members only' }}
          </Button>
          <Button
            variant="subtle"
            @click="confirmOpenToAll = false"
          >
            Cancel
          </Button>
        </div>
      </template>
    </Dialog>
  </section>
</template>
