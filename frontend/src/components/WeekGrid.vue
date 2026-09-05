<script setup>
import { computed } from 'vue'

// P2-U6. The week editor, in both of its shapes.
//
// Replaced, not restyled (the approved canvas). What was here was one flat
// list of `{date, project, task, hours, note}` rows rendered as five
// side-by-side selects, which at 360px stacked into a five-field form per
// hour booked. The canvas answers the same question twice, from **one**
// model:
//
//   phone    day-first. The week spine (the page) picks a day; only that
//            day's lines render, hours move in 0.25 steps on a stepper.
//   >= lg    the project x day grid: one row per project/task, seven cells,
//            day totals with bars underneath, the note at the end.
//
// A *line* is a project + task + note the employee books time against, and
// its `hours` map is keyed by calendar date. A key being present is what
// puts the line on that day, even at zero hours -- "I added this project to
// Thursday and haven't typed the hours yet" is a real state, and a model
// that only stores non-zero hours cannot hold it.
//
// Nothing here mutates the model: every edit is an event, so the page owns
// rounding, clamping and dirty state in one place.
const props = defineProps({
  /** `[{ id, project, task, note, hours: { '2026-09-03': 4 } }]`. */
  lines: { type: Array, required: true },
  /** `get_my_projects`, each with its own `tasks`. */
  projects: { type: Array, required: true },
  /** `[{ iso, weekday, dayOfMonth, isWeekend, total }]`, Monday first. */
  days: { type: Array, required: true },
  /** The day the phone layout is showing. */
  selectedDate: { type: String, default: '' },
  /** What a full working week reads against, for the day-total row. */
  fullWeekHours: { type: Number, default: 40 },
  readOnly: { type: Boolean, default: false },
})

const emit = defineEmits(['add-line', 'remove-line', 'set-hours', 'update-line', 'copy-day'])

const STEP = 0.25
const MAX_HOURS = 24

function tasksFor(projectName) {
  return props.projects.find((p) => p.name === projectName)?.tasks || []
}

function projectLabel(line) {
  const project = props.projects.find((p) => p.name === line.project)
  return project?.project_name || project?.name || line.project || ''
}

const projectOptions = computed(() => [
  { label: 'Pick a project', value: '' },
  ...props.projects.map((p) => ({ label: p.project_name || p.name, value: p.name })),
])

function taskOptions(line) {
  return [
    { label: 'No task', value: '' },
    ...tasksFor(line.project).map((t) => ({ label: t.subject, value: t.name })),
  ]
}

// --- the selected day (phone) -------------------------------------------

const selectedDay = computed(() => props.days.find((day) => day.iso === props.selectedDate) || null)

const dayLines = computed(() =>
  props.lines.filter((line) => line.hours[props.selectedDate] !== undefined),
)

/** The day before the selected one, inside this week, that has anything on
 * it -- the source of "Same projects as Wednesday?". */
const copySource = computed(() => {
  const index = props.days.findIndex((day) => day.iso === props.selectedDate)
  for (let i = index - 1; i >= 0; i -= 1) {
    const day = props.days[i]
    if (props.lines.some((line) => line.hours[day.iso] > 0)) return day
  }
  return null
})

function hoursOn(line, iso) {
  const value = line.hours[iso]
  return value === undefined ? null : value
}

function step(line, iso, direction) {
  const current = hoursOn(line, iso) || 0
  const next = Math.round((current + direction * STEP) * 100) / 100
  emit('set-hours', { id: line.id, date: iso, value: Math.min(MAX_HOURS, Math.max(0, next)) })
}

function typeHours(line, iso, event) {
  const raw = event.target.value
  emit('set-hours', { id: line.id, date: iso, value: raw === '' ? 0 : Number(raw) })
}

/** A grid cell the employee has not put this project on yet stays blank
 * rather than printing 0: an unbooked Saturday and a Saturday booked at
 * zero hours are different sentences. */
function cellValue(line, iso) {
  const value = hoursOn(line, iso)
  return value === null || value === 0 ? '' : value
}
</script>

<template>
  <div>
    <!-- Phone: day-first ------------------------------------------------ -->
    <div
      class="lg:hidden"
      data-testid="week-days"
    >
      <div
        v-if="selectedDay"
        class="mb-3 flex items-baseline justify-between gap-3"
      >
        <h2 class="type-section text-ink-gray-9">
          {{ selectedDay.weekday }}, {{ selectedDay.label }}
        </h2>
        <p class="text-sm text-ink-gray-6">
          <span class="tabular text-xl font-semibold text-ink-gray-9">{{ selectedDay.total.toFixed(1) }}</span>
          h
        </p>
      </div>

      <ul class="space-y-2">
        <li
          v-for="line in dayLines"
          :key="line.id"
          class="surface-card elev-1 flex items-center gap-3 p-3"
        >
          <div class="min-w-0 flex-1">
            <!-- The project reads as the row's title and *is* the control.
                 A borderless select keeps the artboard's plain-text row
                 without hiding the only way to change it behind an edit
                 mode. -->
            <select
              v-if="!readOnly"
              class="-ml-1 w-full cursor-pointer appearance-none truncate rounded-md border-0 bg-transparent bg-none px-1 py-0.5 font-semibold text-ink-gray-9"
              :value="line.project"
              :aria-label="`Project for row ${line.id}`"
              @change="emit('update-line', { id: line.id, field: 'project', value: $event.target.value })"
            >
              <option
                v-for="option in projectOptions"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>
            <p
              v-else
              class="truncate font-semibold text-ink-gray-9"
            >
              {{ projectLabel(line) }}
            </p>

            <select
              v-if="!readOnly"
              class="-ml-1 w-full cursor-pointer appearance-none truncate rounded-md border-0 bg-transparent bg-none px-1 py-0.5 text-sm text-ink-gray-6"
              :value="line.task"
              :aria-label="`Task for row ${line.id}`"
              @change="emit('update-line', { id: line.id, field: 'task', value: $event.target.value })"
            >
              <option
                v-for="option in taskOptions(line)"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>
            <p
              v-else-if="line.task"
              class="truncate text-sm text-ink-gray-6"
            >
              {{ tasksFor(line.project).find((t) => t.name === line.task)?.subject || line.task }}
            </p>

            <input
              v-if="!readOnly"
              class="-ml-1 w-full appearance-none rounded-md border-0 bg-transparent px-1 py-0.5 text-sm italic text-ink-gray-6 placeholder:underline placeholder:decoration-ink-gray-4"
              type="text"
              :value="line.note"
              placeholder="Add a note"
              :aria-label="`Note for row ${line.id}`"
              @input="emit('update-line', { id: line.id, field: 'note', value: $event.target.value })"
            >
            <p
              v-else-if="line.note"
              class="text-sm italic text-ink-gray-6"
            >
              {{ line.note }}
            </p>
          </div>

          <!-- The stepper. 0.25 is the unit the whole product counts in,
               so it is the unit of the control too; the number stays
               typeable for the person booking 6.75. -->
          <div
            v-if="!readOnly"
            class="flex shrink-0 items-center rounded-lg border border-outline-gray-2"
          >
            <button
              class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-l-lg text-lg text-ink-gray-7 hover:bg-surface-gray-2"
              type="button"
              :aria-label="`Less time on ${projectLabel(line)}`"
              @click="step(line, selectedDate, -1)"
            >
              &minus;
            </button>
            <input
              class="tabular w-14 border-x border-outline-gray-2 bg-transparent py-2 text-center text-lg font-semibold text-ink-gray-9"
              type="number"
              inputmode="decimal"
              :step="STEP"
              min="0"
              :max="MAX_HOURS"
              :value="hoursOn(line, selectedDate)"
              :aria-label="`Hours on ${projectLabel(line)}`"
              @input="typeHours(line, selectedDate, $event)"
            >
            <button
              class="flex h-11 w-11 cursor-pointer items-center justify-center rounded-r-lg text-lg text-ink-gray-7 hover:bg-surface-gray-2"
              type="button"
              :aria-label="`More time on ${projectLabel(line)}`"
              @click="step(line, selectedDate, 1)"
            >
              +
            </button>
          </div>
          <p
            v-else
            class="tabular shrink-0 text-lg font-semibold text-ink-gray-9"
          >
            {{ (hoursOn(line, selectedDate) || 0).toFixed(2) }}
          </p>

          <button
            v-if="!readOnly"
            class="flex h-11 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-gray-4 hover:bg-surface-gray-2 hover:text-ink-red-4"
            type="button"
            :aria-label="`Remove ${projectLabel(line)} from ${selectedDay?.weekday}`"
            @click="emit('remove-line', { id: line.id, date: selectedDate })"
          >
            &times;
          </button>
        </li>
      </ul>

      <template v-if="!readOnly">
        <button
          class="mt-2 flex min-h-11 w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-dashed border-outline-gray-3 px-3 py-3 font-medium text-ink-blue-link hover:bg-surface-gray-2"
          type="button"
          @click="emit('add-line', selectedDate)"
        >
          + Add time to {{ selectedDay?.weekday }}
        </button>

        <p
          v-if="copySource"
          class="mt-3 text-center text-sm text-ink-gray-6"
        >
          Same projects as {{ copySource.weekday }}?
          <button
            class="cursor-pointer font-medium text-ink-blue-link underline underline-offset-2"
            type="button"
            @click="emit('copy-day', copySource.iso)"
          >
            Copy them
          </button>
        </p>
      </template>

      <p
        v-else-if="!dayLines.length"
        class="surface-inset p-4 text-center text-sm text-ink-gray-6"
      >
        Nothing booked on {{ selectedDay?.weekday }}.
      </p>
    </div>

    <!-- Desktop: the project x day grid ---------------------------------- -->
    <div
      class="hidden lg:block"
      data-testid="week-grid"
    >
      <div class="surface-card elev-1 overflow-x-auto">
        <table class="w-full min-w-[52rem] border-collapse text-sm">
          <thead>
            <tr class="border-b border-outline-gray-1">
              <th
                class="label px-4 py-3 text-left"
                scope="col"
              >
                Project / Task
              </th>
              <th
                v-for="day in days"
                :key="day.iso"
                class="label px-1.5 py-3 text-center"
                :class="day.isWeekend ? 'bg-surface-gray-2/70' : ''"
                scope="col"
              >
                {{ day.weekday }} {{ day.dayOfMonth }}
              </th>
              <th
                class="label px-3 py-3 text-right"
                scope="col"
              >
                Total
              </th>
              <th
                class="label px-4 py-3 text-left"
                scope="col"
              >
                Note
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="line in lines"
              :key="line.id"
              class="border-b border-outline-gray-1"
            >
              <th
                class="w-56 min-w-56 px-4 py-2 text-left font-normal"
                scope="row"
              >
                <select
                  v-if="!readOnly"
                  class="-ml-1 w-full cursor-pointer appearance-none truncate rounded-md border-0 bg-transparent bg-none px-1 py-0.5 font-semibold text-ink-gray-9"
                  :value="line.project"
                  :aria-label="`Project for row ${line.id}`"
                  @change="emit('update-line', { id: line.id, field: 'project', value: $event.target.value })"
                >
                  <option
                    v-for="option in projectOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
                <p
                  v-else
                  class="font-semibold text-ink-gray-9"
                >
                  {{ projectLabel(line) }}
                </p>
                <select
                  v-if="!readOnly"
                  class="-ml-1 w-full cursor-pointer appearance-none truncate rounded-md border-0 bg-transparent bg-none px-1 py-0.5 text-ink-gray-6"
                  :value="line.task"
                  :aria-label="`Task for row ${line.id}`"
                  @change="emit('update-line', { id: line.id, field: 'task', value: $event.target.value })"
                >
                  <option
                    v-for="option in taskOptions(line)"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </option>
                </select>
                <p
                  v-else-if="line.task"
                  class="text-ink-gray-6"
                >
                  {{ tasksFor(line.project).find((t) => t.name === line.task)?.subject || line.task }}
                </p>
              </th>
              <td
                v-for="day in days"
                :key="day.iso"
                class="px-1.5 py-2 text-center"
                :class="day.isWeekend ? 'bg-surface-gray-2/70' : ''"
              >
                <input
                  v-if="!readOnly"
                  class="tabular w-14 rounded-lg border border-outline-gray-2 bg-surface-white px-1 py-2 text-center text-ink-gray-9"
                  type="number"
                  inputmode="decimal"
                  :step="STEP"
                  min="0"
                  :max="MAX_HOURS"
                  placeholder="–"
                  :value="cellValue(line, day.iso)"
                  :aria-label="`${projectLabel(line)}, ${day.weekday} ${day.dayOfMonth}`"
                  @input="typeHours(line, day.iso, $event)"
                >
                <span
                  v-else
                  class="tabular text-ink-gray-9"
                >{{ cellValue(line, day.iso) || '–' }}</span>
              </td>
              <td class="tabular px-3 py-2 text-right font-semibold text-ink-gray-9">
                {{ Object.values(line.hours).reduce((sum, value) => sum + (value || 0), 0).toFixed(1) }}
              </td>
              <td class="px-4 py-2">
                <input
                  v-if="!readOnly"
                  class="w-full min-w-28 appearance-none rounded-md border-0 bg-transparent px-1 py-1 italic text-ink-gray-6"
                  type="text"
                  :value="line.note"
                  placeholder="Add a note"
                  :aria-label="`Note for row ${line.id}`"
                  @input="emit('update-line', { id: line.id, field: 'note', value: $event.target.value })"
                >
                <span
                  v-else
                  class="italic text-ink-gray-6"
                >{{ line.note }}</span>
              </td>
            </tr>

            <tr class="surface-inset">
              <th
                class="px-4 py-3 text-left font-medium text-ink-gray-9"
                scope="row"
              >
                Day total
              </th>
              <td
                v-for="day in days"
                :key="day.iso"
                class="px-1.5 py-3 text-center align-top"
                :class="day.isWeekend ? 'bg-surface-gray-3/50' : ''"
              >
                <span class="tabular block font-semibold text-ink-gray-9">{{ day.total.toFixed(0) }}</span>
                <!-- The bar is read against an 8h day, the same rule the
                     dashboard week spine uses, so a full day looks full on
                     both screens. -->
                <span class="mt-1 block h-1 w-full rounded-full bg-surface-gray-3">
                  <span
                    class="block h-1 rounded-full bg-surface-green-3"
                    :style="{ width: `${Math.min(100, (day.total / 8) * 100)}%` }"
                  />
                </span>
              </td>
              <td class="tabular px-3 py-3 text-right font-semibold text-ink-gray-9">
                {{ days.reduce((sum, day) => sum + day.total, 0).toFixed(1) }}
              </td>
              <td class="tabular px-4 py-3 text-sm text-ink-gray-6">
                of {{ fullWeekHours }} h
              </td>
            </tr>
          </tbody>
        </table>

        <div
          v-if="!readOnly"
          class="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3"
        >
          <button
            class="cursor-pointer font-medium text-ink-blue-link hover:underline"
            type="button"
            @click="emit('add-line', null)"
          >
            + Add a project row
          </button>
          <p class="text-sm text-ink-gray-6">
            Only projects you're booked on appear here. Missing one?
            <router-link
              class="font-medium text-ink-blue-link underline underline-offset-2"
              :to="{ name: 'Requests', query: { category: 'Other', subject: 'Please add me to a project I book time on' } }"
            >
              Ask HR
            </router-link>
          </p>
        </div>
      </div>
    </div>
  </div>
</template>
