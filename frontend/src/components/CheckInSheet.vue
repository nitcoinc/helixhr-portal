<script setup>
import { ref, computed, watch } from 'vue'
import { createResource, Dialog, Button } from 'frappe-ui'
import Icon from '@/components/Icon.vue'
import {
  getPosition,
  GEO_DENIED,
  GEO_INSECURE,
  GEO_TIMEOUT,
  GEO_UNAVAILABLE,
  GEO_UNSUPPORTED,
} from '@/lib/geolocation'

// P3-U4 step 4 / P3-R5, P3-R6, P3-KTD4. The punch sheet.
//
// Four states, because four things can happen and three of them need
// different advice:
//
//   confirm       we have a fix. Its accuracy, the disclosure, and the button.
//   locating      the browser is working on it; the button says so and does
//                 nothing.
//   blocked       no fix, and no punch. Which sentence depends on *why* --
//                 a browser setting, a plain-HTTP page and a cold GPS are
//                 three different problems and only one of them is fixed in
//                 a settings screen.
//   out-of-range  a fix, a punch, and HRMS refused it because the employee
//                 is not at the shift location. Its distance message is
//                 shown verbatim and the allow-location copy is never shown
//                 here: location worked perfectly.
//
// Location is asked for when the sheet opens, which is a tap on the strip and
// never a page load (P3-R6). It is captured once, at that tap, and is not
// watched between punches -- which is exactly what the notice says.

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 'IN' or 'OUT' -- what the server derived when the page loaded. It is
   * sent back so the server can refuse a punch decided against a stale
   * screen (P3-KTD3). */
  logType: { type: String, default: 'IN' },
  /** Where "Fix a day" goes. A router location, passed in by the page so the
   * pointer always carries the day it is about. */
  fixADayTo: { type: [Object, String], default: null },
})
const emit = defineEmits(['update:modelValue', 'punched'])

const STATE = { LOCATING: 'locating', CONFIRM: 'confirm', BLOCKED: 'blocked', RANGE: 'range' }

const state = ref(STATE.LOCATING)
const position = ref(null)
const blockedKind = ref(GEO_UNAVAILABLE)
const message = ref('')
const punching = ref(false)

const punch = createResource({ url: 'helixhr.api.punch_my_checkin', method: 'POST' })

const isCheckIn = computed(() => props.logType !== 'OUT')
const actionLabel = computed(() => (isCheckIn.value ? 'Check in' : 'Check out'))
const title = computed(() => actionLabel.value)

/** Plain words per kind. "Allow location" is advice about a browser setting,
 * so it is only ever said when a browser setting is the problem. */
const BLOCKED_COPY = {
  [GEO_DENIED]: {
    title: 'Your browser is blocking location for this site',
    body: 'Tap the padlock next to the address (on a phone: Settings, then Site settings, then Location), allow location for this site, then try again.',
    retry: true,
  },
  [GEO_UNAVAILABLE]: {
    title: "Your device couldn't work out where it is",
    body: 'This usually clears up outdoors or near a window. Try again in a moment.',
    retry: true,
  },
  [GEO_TIMEOUT]: {
    title: 'Finding your location took too long',
    body: 'Your device is still looking. Try again — it is usually quicker the second time.',
    retry: true,
  },
  [GEO_UNSUPPORTED]: {
    title: "This browser can't share a location",
    body: 'Open the portal in another browser to punch, or ask HR to fix the day for you.',
    retry: false,
  },
  [GEO_INSECURE]: {
    title: 'Location needs a secure connection',
    body: 'This page was opened over plain http, and no browser will share a location there. Open the portal over https and try again.',
    retry: false,
  },
}

const blocked = computed(() => BLOCKED_COPY[blockedKind.value] || BLOCKED_COPY[GEO_UNAVAILABLE])

/** "±18 m". Rounded: a fix is not accurate to the centimetre and printing
 * one implies it is. */
const accuracyLabel = computed(() => {
  const accuracy = position.value?.accuracy
  if (!Number.isFinite(accuracy)) return null
  return `±${Math.round(accuracy)} m`
})

/** HRMS's own geofence refusal, told apart from every other error by the
 * sentence it ships: "You must be within N meters of your shift location to
 * check in." (P3-U4 step 4). */
const DISTANCE_MESSAGE = /within\s+[\d.]+\s*met(er|re)s?\s+of your shift location/i

function plainError(error) {
  const raw = error?.messages?.[0] || error?.message || ''
  return String(raw)
    .replace(/<[^>]*>/g, '')
    .trim()
}

// P3-R6. One fix per opening of the sheet, and only the current opening's
// fix is allowed to land. A high-accuracy request runs for up to ten seconds
// (lib/geolocation.js), which is long enough to close the sheet and reopen
// it: the superseded promise then resolves last and overwrites `position` and
// `state`, which showed the allow-location advice over a perfectly good fix
// and -- worse -- left a coordinate from the previous attempt on screen for
// the punch to send at a geofence. The counter is bumped on every locate and
// on every close, so a result from any earlier generation is dropped.
let locateGeneration = 0

async function locate() {
  const generation = (locateGeneration += 1)
  state.value = STATE.LOCATING
  message.value = ''
  position.value = null
  try {
    const fix = await getPosition()
    if (generation !== locateGeneration) return
    position.value = fix
    state.value = STATE.CONFIRM
  } catch (error) {
    if (generation !== locateGeneration) return
    blockedKind.value = error?.kind || GEO_UNAVAILABLE
    state.value = STATE.BLOCKED
  }
}

async function submit() {
  if (!position.value || punching.value) return
  punching.value = true
  message.value = ''
  try {
    const result = await punch.submit({
      latitude: position.value.latitude,
      longitude: position.value.longitude,
      expected_log_type: props.logType,
    })
    emit('punched', result)
    emit('update:modelValue', false)
  } catch (error) {
    const plain = plainError(error)
    if (DISTANCE_MESSAGE.test(plain)) {
      // Location worked. The employee is somewhere else.
      message.value = plain
      state.value = STATE.RANGE
    } else {
      message.value = plain || 'That did not go through. Try again.'
    }
  } finally {
    punching.value = false
  }
}

// Opening the sheet *is* the tap, so this is where the one location request
// of the whole page happens.
watch(
  () => props.modelValue,
  (open) => {
    if (open) locate()
    // Closing retires the in-flight fix rather than letting it land later.
    else locateGeneration += 1
  },
)

const open = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})
</script>

<template>
  <Dialog
    v-model="open"
    :options="{ title, size: 'sm' }"
  >
    <template #body-content>
      <!-- Locating and confirm are one region: the same button, disabled
           while the fix is being taken, so the sheet does not reflow under
           the thumb the moment a position arrives. -->
      <div v-if="state === STATE.LOCATING || state === STATE.CONFIRM">
        <p class="text-sm text-ink-gray-7">
          <template v-if="state === STATE.LOCATING">
            Getting your location…
          </template>
          <template v-else>
            Location captured
            <span
              v-if="accuracyLabel"
              class="tabular text-ink-gray-6"
            >· accurate to about {{ accuracyLabel }}</span>
          </template>
        </p>

        <!-- P3-KTD4. The disclosure. No consent record is kept, so this
             sentence is the disclosure: who reads it, and that it is one
             reading taken now rather than tracking between punches. -->
        <p class="surface-inset mt-3 p-3 text-sm text-ink-gray-7">
          Your location is saved with this punch so HR and your manager can see
          where you checked in from. It is read once, when you tap the button
          below — the portal does not follow you between punches.
        </p>

        <p
          v-if="message"
          class="surface-alert mt-3 p-3 text-sm"
          role="alert"
        >
          {{ message }}
        </p>

        <div class="mt-4 flex flex-wrap items-center gap-2">
          <Button
            variant="solid"
            theme="blue"
            :loading="punching"
            :disabled="state === STATE.LOCATING || punching"
            @click="submit"
          >
            {{ state === STATE.LOCATING ? 'Locating…' : actionLabel }}
          </Button>
          <Button
            variant="subtle"
            @click="open = false"
          >
            Cancel
          </Button>
        </div>
      </div>

      <!-- No location, no punch (P3-R6). -->
      <div v-else-if="state === STATE.BLOCKED">
        <h3 class="text-sm font-medium text-ink-gray-9">
          {{ blocked.title }}
        </h3>
        <p class="mt-1 text-sm text-ink-gray-7">
          {{ blocked.body }}
        </p>
        <p class="mt-3 text-sm text-ink-gray-7">
          Nothing was recorded. If you cannot punch at all today, ask for the
          day to be fixed instead.
        </p>
        <div class="mt-4 flex flex-wrap items-center gap-2">
          <Button
            v-if="blocked.retry"
            variant="solid"
            theme="blue"
            @click="locate"
          >
            Try again
          </Button>
          <router-link
            v-if="fixADayTo"
            class="flex min-h-11 items-center rounded-md border border-outline-gray-2 px-4 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
            :to="fixADayTo"
          >
            Fix a day
          </router-link>
        </div>
      </div>

      <!-- Out of range: HRMS's own message, unchanged. It names the radius,
           which is the one number that makes the refusal actionable. -->
      <div v-else>
        <h3 class="flex items-center gap-2 text-sm font-medium text-ink-gray-9">
          <Icon
            name="pin"
            class="h-4 w-4 text-ink-gray-6"
          />
          You are too far from your shift location
        </h3>
        <p
          class="mt-1 text-sm text-ink-gray-7"
          role="alert"
        >
          {{ message }}
        </p>
        <p class="mt-3 text-sm text-ink-gray-7">
          Nothing was recorded. Move closer and try again, or ask for the day
          to be fixed.
        </p>
        <div class="mt-4 flex flex-wrap items-center gap-2">
          <Button
            variant="solid"
            theme="blue"
            @click="locate"
          >
            Try again
          </Button>
          <router-link
            v-if="fixADayTo"
            class="flex min-h-11 items-center rounded-md border border-outline-gray-2 px-4 text-sm font-medium text-ink-gray-8 hover:bg-surface-gray-2"
            :to="fixADayTo"
          >
            Fix a day
          </router-link>
        </div>
      </div>
    </template>
  </Dialog>
</template>
