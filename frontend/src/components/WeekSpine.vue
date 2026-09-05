<script setup>
import { computed } from 'vue'
import Icon from '@/components/Icon.vue'
import { ATTENDANCE_LABEL, barHeight } from '@/lib/week'

const props = defineProps({
  week: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})

// The words come from lib/week.js so a green marker means the same thing here
// and on Attendance. The dots do not: these sit on the deep blue field, so the
// tints are the light end of each family, not the -600 steps that read on
// white.
const DOT = {
  Present: 'bg-green-200',
  'Half Day': 'bg-amber-300',
  Absent: 'bg-red-300',
  'On Leave': 'bg-blue-200',
  Holiday: 'bg-white/40',
}

const days = computed(() => props.week?.days || [])
const totalHours = computed(() => props.week?.total_hours || 0)
// With nothing logged, the bar track is 150px of blank that reads as a chart
// which failed to load. A week with no hours has no shape to show, so the
// track collapses and the spine goes back to being a compact strip.
const hasHours = computed(() => totalHours.value > 0)

// The week this spine is drawing, by its Monday -- the same identity the
// server uses and the route takes (P2-U2, P2-R12). "/timesheet" resolves to
// whatever week is current when the link is followed, which is only the same
// answer by accident.
const weekRoute = computed(() =>
  props.week?.week_start
    ? { name: 'TimesheetWeek', params: { weekStart: props.week.week_start } }
    : { name: 'Timesheet' },
)

/** The day's attendance status, or null when it has none. */
function stateFor(day) {
  if (day.on_leave) return 'On Leave'
  return day.attendance && DOT[day.attendance] ? day.attendance : null
}

function isWeekend(day) {
  return day.weekday === 'Sat' || day.weekday === 'Sun'
}

function dotClass(day) {
  const state = stateFor(day)
  if (state) return DOT[state]
  if (day.is_future || isWeekend(day)) return 'bg-transparent'
  return 'border border-white/40 bg-transparent'
}

function dayLabel(day) {
  const state = stateFor(day)
  const parts = [`${day.weekday} ${day.day_of_month}`]
  if (state) parts.push(ATTENDANCE_LABEL[state])
  if (day.hours) parts.push(`${day.hours} hours`)
  return parts.join(', ')
}
</script>

<template>
  <!-- The committed field. The direction's thesis is "the working week is the
       page", and on a page of white cards the spine was making that argument
       in the same voice as everything else. Drenched in the brand blue it is
       the one region that owns its colour, and every other surface on the
       screen reads as resting on the page rather than competing with it.
       elev-2 because this is the surface the page is anchored by. -->
  <section
    class="elev-2 overflow-hidden rounded-xl bg-field"
    aria-label="This week"
  >
    <div
      v-if="loading"
      class="flex gap-1 p-3"
      aria-busy="true"
    >
      <div
        v-for="n in 7"
        :key="n"
        class="h-20 flex-1 animate-pulse rounded-lg bg-white/10"
      />
    </div>

    <div v-else-if="week">
      <div class="grid grid-cols-7">
        <!-- Seven equal cells, never a scroller: the whole point of the spine
             is that the week is graspable in one look, including at 360px. -->
        <router-link
          v-for="day in days"
          :key="day.date"
          :to="weekRoute"
          class="relative flex cursor-pointer flex-col items-center gap-1 border-r border-white/10 px-1 pb-2 pt-2.5 transition-colors duration-200 last:border-r-0 hover:bg-white/10"
          :class="[
            day.is_today ? 'bg-white/15' : '',
            !day.is_today && isWeekend(day) ? 'bg-field-deep/50' : '',
          ]"
          :aria-label="dayLabel(day)"
        >
          <!-- Today is a solid rule across the top of its cell, not only a
               tint: hover uses a tint too, and a rule is the one mark hover
               cannot counterfeit. -->
          <span
            v-if="day.is_today"
            class="absolute inset-x-0 top-0 h-0.5 bg-signal"
          />
          <span
            class="text-[11px] font-medium uppercase tracking-wide"
            :class="day.is_today ? 'text-white' : isWeekend(day) ? 'text-blue-300' : 'text-blue-200'"
          >
            {{ day.weekday }}
          </span>
          <span
            class="tabular text-sm font-medium"
            :class="day.is_today ? 'text-white' : isWeekend(day) ? 'text-blue-200' : 'text-white'"
          >
            {{ day.day_of_month }}
          </span>
          <!-- A working day with nothing recorded is a hollow ring, not an
               invisible dot: "no record yet" and "nothing expected" have to
               look different at a glance. -->
          <span
            class="h-2 w-2 rounded-full"
            :class="dotClass(day)"
          />
          <template v-if="hasHours">
            <!-- Width is a fraction of the cell, not a fixed 20px: at 1440 a
                 fixed bar is a sliver adrift in a 140px cell, so the week's
                 shape stopped reading at exactly the width with most room
                 for it. -->
            <span class="mt-1 flex h-16 w-full items-end justify-center">
              <span
                class="w-1/2 max-w-10 rounded-sm"
                :class="day.hours ? 'bg-signal' : 'bg-transparent'"
                :style="{ height: `${barHeight(day.hours)}%` }"
              />
            </span>
            <span
              class="tabular text-[11px]"
              :class="day.hours ? 'text-blue-100' : 'text-transparent'"
            >
              {{ day.hours ? `${day.hours}h` : '0' }}
            </span>
          </template>
        </router-link>
      </div>

      <div class="flex items-center justify-between gap-3 bg-field-deep/60 px-4 py-2.5">
        <p class="text-sm text-blue-100">
          <span class="tabular font-semibold text-white">{{ totalHours }}</span>
          {{ totalHours === 1 ? 'hour' : 'hours' }} logged this week
        </p>
        <router-link
          :to="weekRoute"
          class="inline-flex min-h-11 cursor-pointer items-center gap-1 text-sm font-medium text-signal hover:underline"
        >
          Timesheet
          <Icon
            name="chevronRight"
            size="h-4 w-4"
          />
        </router-link>
      </div>
    </div>
  </section>
</template>
