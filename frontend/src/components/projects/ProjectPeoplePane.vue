<script setup>
// Plan 2026-10-31-001 U3 / U6 (R3, R8). A project's people: search, show
// more, remove, "Add people" (ProjectMemberPicker) and the "Open to
// everyone" switch. The parent owns `get_project`; this pane hands back what
// changed as `update:members` / `update:open`. A write that lands after the
// route moved to another project is dropped, so it never lands on the wrong
// one.
//
// Plan 2026-11-01-001 U5 (R1, R2, R7). "Add people" and the switch sit in
// the header. The switch names more companies than the project's own, and
// only on a non-billable project: a billable one that is already open shows
// it so it can be turned off, never on.
import { computed, ref, toRef, watch } from 'vue'
import { createResource, Button, Dialog } from 'frappe-ui'
import Avatar from '@/components/Avatar.vue'
import ProjectMemberPicker from '@/components/ProjectMemberPicker.vue'
import SearchField from '@/components/SearchField.vue'
import { listWords } from '@/lib/projects'
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
  billable: { type: Boolean, default: false },
  /** `get_project().status`: booking only happens while it is Open. */
  status: { type: String, default: '' },
  /** `get_project().open_companies`: who can already book, own company
   * first; empty unless the project is Open and open to everyone. */
  openCompanies: { type: Array, default: () => [] },
  /** `get_project().open_company_choices`: companies this editor may tick. */
  openCompanyChoices: { type: Array, default: () => [] },
  /** `get_project().stored_open_companies`: every saved extra company,
   * whatever the status; the dialog starts from these. */
  storedOpenCompanies: { type: Array, default: () => [] },
  /** `get_project().fixed_open_companies`: stored ones outside this editor's
   * access, which their edits keep. */
  fixedOpenCompanies: { type: Array, default: () => [] },
  /** The caller's own company, whose people the picker's search covers. */
  callerCompany: { type: String, default: '' },
})
const emit = defineEmits(['update:members', 'update:open'])

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

// --- "Open to everyone" (U6, R8; 2026-11-01-001 U5) -------------------------
//
// Every change goes through one dialog that names its effect: turning on
// (pick the extra companies), editing the companies while on, or turning
// off. The dialog stays open while it saves and shows a refusal inline; the
// switch shows the pending state meanwhile.

const showSwitch = computed(() => props.canEdit && (!props.billable || props.openToAll))
// The saved list (own company first), whatever the project's status:
// `open_companies` is only reported while it is Open, so the note and the
// dialog read `stored_open_companies` and stay right on a closed project.
const configured = computed(() => [...new Set([props.companyName, ...props.storedOpenCompanies])])
const canEditCompanies = computed(() => props.canEdit && !props.billable && props.openToAll)
const extraCount = computed(() => props.storedOpenCompanies.length)
const switchLabel = computed(() =>
  props.openToAll && extraCount.value
    ? `Open to everyone in ${props.companyName} and ${extraCount.value} more`
    : `Open to everyone in ${props.companyName}`,
)
/** The saved extra companies inside this editor's scope: the ticked boxes. */
const storedPicks = computed(() =>
  props.storedOpenCompanies.filter((company) => !props.fixedOpenCompanies.includes(company)),
)

const dialogOpen = ref(false)
// 'on' | 'edit' | 'off'; kept while the dialog closes so its title holds.
const dialogMode = ref('on')
const picks = ref([])
const dialogError = ref('')
const openPending = ref(null)
const openError = ref('')
const setOpenToAll = createResource({ url: 'helixhr.api.set_project_open_to_all', method: 'POST' })
const switchOn = computed(() => openPending.value ?? props.openToAll)

const pickedSet = computed(() => new Set(picks.value))
const orderedPicks = computed(() => props.openCompanyChoices.filter((company) => pickedSet.value.has(company)))
const willCover = computed(() => [props.companyName, ...props.fixedOpenCompanies, ...orderedPicks.value])
const removedPicks = computed(() =>
  dialogMode.value === 'edit' ? storedPicks.value.filter((company) => !pickedSet.value.has(company)) : [],
)

const DIALOG = {
  on: { title: 'Open to everyone?', action: 'Open to everyone' },
  edit: { title: 'Companies open to everyone', action: 'Save companies' },
  off: { title: 'Only listed members?', action: 'Members only' },
}

function startOpenChange(mode) {
  if (openPending.value !== null) return
  openError.value = ''
  dialogError.value = ''
  dialogMode.value = mode
  picks.value = mode === 'edit' ? [...storedPicks.value] : []
  dialogOpen.value = true
}

function askOpenToAll() {
  startOpenChange(props.openToAll ? 'off' : 'on')
}

function togglePick(company, checked) {
  picks.value = checked ? [...new Set([...picks.value, company])] : picks.value.filter((row) => row !== company)
}

async function applyOpenToAll() {
  if (openPending.value !== null) return
  const project = props.project
  const target = dialogMode.value !== 'off'
  const params = { project, value: target ? 1 : 0 }
  if (target) params.companies = orderedPicks.value
  dialogError.value = ''
  openPending.value = target
  try {
    const response = await setOpenToAll.submit(params)
    if (props.project !== project) return
    emit('update:open', {
      open_to_all: !!response?.open_to_all,
      open_companies: response?.open_companies || [],
      fixed_open_companies: response?.fixed_open_companies || [],
      stored_open_companies: response?.stored_open_companies || [],
    })
    dialogOpen.value = false
  } catch (error) {
    if (props.project !== project) return
    const message =
      error?.messages?.[0] || `Could not change that. The project is still ${props.openToAll ? 'open to everyone' : 'members only'}.`
    // Closed while it saved: say it on the pane instead.
    if (dialogOpen.value) dialogError.value = message
    else openError.value = message
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
    dialogOpen.value = false
    dialogError.value = ''
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
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2">
      <h2
        id="project-members-heading"
        class="mr-auto font-heading text-base font-semibold text-ink-gray-9"
      >
        People <span class="tabular text-sm font-normal text-ink-gray-5">{{ members.length }}</span>
      </h2>
      <Button
        v-if="canEdit"
        class="min-h-11 max-sm:w-full"
        variant="outline"
        data-testid="project-add-people"
        @click="pickerOpen = true"
      >
        Add people
      </Button>
      <button
        v-if="showSwitch"
        type="button"
        role="switch"
        class="flex min-h-11 cursor-pointer items-center gap-3 text-left text-sm font-medium text-ink-gray-9 aria-disabled:cursor-progress max-sm:w-full"
        :aria-checked="switchOn ? 'true' : 'false'"
        :aria-disabled="openPending !== null ? 'true' : undefined"
        :aria-busy="openPending !== null ? 'true' : undefined"
        :aria-describedby="billable ? 'project-open-billable' : undefined"
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
        <span class="break-words">{{ switchLabel }}</span>
        <span
          v-if="openPending !== null"
          class="text-xs font-normal text-ink-gray-5"
        >Saving…</span>
      </button>
    </div>

    <p
      v-if="canEdit && billable"
      id="project-open-billable"
      class="mt-2 text-sm text-ink-gray-6"
      data-testid="project-open-billable"
    >
      {{ openToAll ? 'Billable projects can only be turned off.' : "Billable projects can't be opened to everyone." }}
    </p>
    <p
      v-if="openError"
      class="surface-alert mt-3 p-3 text-sm"
      role="alert"
    >
      {{ openError }}
    </p>
    <div
      v-if="openToAll"
      class="mt-2 flex flex-wrap items-center gap-x-2 text-sm text-ink-gray-7"
      data-testid="project-open-note"
    >
      <p class="min-w-0 break-words">
        <template v-if="canEdit">
          Open to {{ listWords(configured) }}. Everyone active there can book time here{{
            status && status !== 'Open' ? ' while the project is Open' : ''
          }}. People below were added by name.
        </template>
        <!-- A view-only caller is not sent the company list. -->
        <template v-else>
          Everyone active in {{ companyName }}, and in any other company it is open to, can book time here. People
          below were added by name.
        </template>
      </p>
      <Button
        v-if="canEditCompanies"
        class="min-h-11"
        variant="ghost"
        data-testid="project-open-companies"
        @click="startOpenChange('edit')"
      >
        Edit companies
      </Button>
    </div>

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

    <p
      v-if="memberError"
      class="surface-alert mb-3 p-3 text-sm"
      role="alert"
    >
      {{ memberError }}
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

    <ProjectMemberPicker
      v-if="canEdit"
      v-model="pickerOpen"
      :project="project"
      :departments="departments"
      :member-ids="memberEmployeeIds"
      :open-companies="openCompanies"
      :caller-company="callerCompany"
      @added="onMembersAdded"
    />

    <Dialog
      v-model="dialogOpen"
      :options="{ title: DIALOG[dialogMode].title, size: 'sm' }"
    >
      <template #body-content>
        <div class="space-y-3">
          <fieldset
            v-if="dialogMode !== 'off'"
            data-testid="project-open-company-list"
          >
            <legend class="mb-1 text-sm text-ink-gray-7">
              Companies
            </legend>
            <ul class="max-h-64 divide-y divide-outline-gray-1 overflow-y-auto">
              <li>
                <label class="flex min-h-11 items-center gap-3 px-1 py-1.5 text-sm text-ink-gray-8">
                  <input
                    type="checkbox"
                    class="size-5 shrink-0"
                    checked
                    disabled
                  >
                  <span class="min-w-0 flex-1 break-words">{{ companyName }}</span>
                  <span class="shrink-0 text-xs text-ink-gray-5">This project's company</span>
                </label>
              </li>
              <li
                v-for="company in fixedOpenCompanies"
                :key="`fixed-${company}`"
              >
                <label class="flex min-h-11 items-center gap-3 px-1 py-1.5 text-sm text-ink-gray-8">
                  <input
                    type="checkbox"
                    class="size-5 shrink-0"
                    checked
                    disabled
                  >
                  <span class="min-w-0 flex-1 break-words">{{ company }}</span>
                  <span class="shrink-0 text-xs text-ink-gray-5">Set by someone with wider access</span>
                </label>
              </li>
              <li
                v-for="company in openCompanyChoices"
                :key="company"
              >
                <label
                  class="flex min-h-11 cursor-pointer items-center gap-3 px-1 py-1.5 text-sm text-ink-gray-8"
                  data-testid="project-open-company-option"
                >
                  <input
                    type="checkbox"
                    class="size-5 shrink-0"
                    :checked="pickedSet.has(company)"
                    :disabled="openPending !== null"
                    @change="togglePick(company, $event.target.checked)"
                  >
                  <span class="min-w-0 flex-1 break-words">{{ company }}</span>
                </label>
              </li>
            </ul>
            <p
              v-if="!openCompanyChoices.length"
              class="mt-1 text-sm text-ink-gray-6"
            >
              No other companies in your access.
            </p>
          </fieldset>

          <p
            class="text-sm text-ink-gray-7"
            aria-live="polite"
            data-testid="project-open-confirm"
          >
            <template v-if="dialogMode === 'off'">
              Only listed members can book. Everyone in {{ listWords(configured) }} not listed by name will no longer
              be able to book here. Their draft weeks with this project will need that row removed.
            </template>
            <template v-else>
              Every active employee in {{ listWords(willCover) }} can book time on this project.
            </template>
          </p>
          <p
            v-if="removedPicks.length"
            class="text-sm text-ink-gray-7"
            data-testid="project-open-removed"
          >
            Everyone in {{ listWords(removedPicks) }} not listed by name will no longer be able to book here. Their
            draft weeks with this project will need that row removed.
          </p>

          <p
            v-if="dialogError"
            class="surface-alert p-3 text-sm"
            role="alert"
          >
            {{ dialogError }}
          </p>

          <div class="flex flex-wrap items-center gap-2">
            <Button
              variant="solid"
              theme="blue"
              class="min-h-11"
              :loading="openPending !== null"
              @click="applyOpenToAll"
            >
              {{ DIALOG[dialogMode].action }}
            </Button>
            <Button
              variant="subtle"
              class="min-h-11"
              @click="dialogOpen = false"
            >
              Cancel
            </Button>
          </div>
        </div>
      </template>
    </Dialog>
  </section>
</template>
