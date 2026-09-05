<template>
  <div class="flex min-h-screen items-center justify-center bg-surface-white px-4">
    <div
      class="max-w-sm text-center"
      :data-portal-state="state"
    >
      <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
        {{ copy.heading }}
      </h1>
      <p class="mt-2 text-ink-gray-6">
        {{ copy.body }}
      </p>

      <!-- Unlinked: the one thing this person can do is write to HR. -->
      <a
        v-if="state === 'not-linked' && hrContactEmail"
        :href="`mailto:${hrContactEmail}`"
        class="mt-6 inline-block cursor-pointer text-blue-700 hover:underline"
      >
        {{ hrContactEmail }}
      </a>

      <!-- Service failure: a bounded retry, and it resumes the page they
           actually asked for rather than dropping them on Home (P2-R25). -->
      <button
        v-if="state === 'unavailable'"
        class="mt-6 inline-flex min-h-11 cursor-pointer items-center rounded-lg bg-blue-700 px-4 py-2 font-medium text-white hover:bg-blue-800 disabled:opacity-60"
        :disabled="retrying"
        @click="retry"
      >
        {{ retrying ? 'Retrying…' : 'Retry' }}
      </button>

      <!-- Unknown route: somewhere to go that exists. -->
      <router-link
        v-if="state === 'not-found'"
        to="/"
        class="mt-6 inline-flex min-h-11 cursor-pointer items-center rounded-lg bg-blue-700 px-4 py-2 font-medium text-white hover:bg-blue-800"
      >
        Go to Home
      </router-link>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { retryBootstrap, session } from '@/lib/session'

// P2-U2 scenario 3 and 4. Three different things used to look like one:
// a user HR has not linked to an Employee, a portal that failed to answer,
// and a URL that does not exist all landed on "Your account is not set up".
// Telling a person their account is broken because a request timed out is
// both wrong and unactionable, so each state gets its own words and its own
// single next step. The route decides which; `lib/session.js` decides
// between the first two.
//
// Visual work is not this unit's -- U3 owns the async-state and error
// panels, and will most likely absorb this page into them.
const route = useRoute()
const router = useRouter()
const retrying = ref(false)

const state = computed(() => {
  if (route.name === 'NotFound') return 'not-found'
  if (route.name === 'Unavailable') return 'unavailable'
  return session.status === 'unavailable' ? 'unavailable' : 'not-linked'
})

const COPY = {
  'not-linked': {
    heading: 'Your account is not set up',
    body: 'We could not find an active employee record for your sign-in. Contact HR to get set up.',
  },
  unavailable: {
    heading: 'We could not load your portal',
    body: 'Something went wrong on our side, not with your account. Try again in a moment.',
  },
  'not-found': {
    heading: 'That page does not exist',
    body: 'The link may be old, or the address may have a typo in it.',
  },
}

const copy = computed(() => COPY[state.value])

// Site configuration, not a constant: helixhr/www/helixhr.py injects the
// `helixhr_hr_contact` site-config key as a window global when it serves
// the shell. Unset means the page says "Contact HR" with no link rather
// than pointing at a made-up address.
const hrContactEmail = window.helixhr_hr_contact || ''

async function retry() {
  retrying.value = true
  try {
    await retryBootstrap()
    // Back to whatever the failed navigation was aiming at, or Home.
    const to = route.query['retry-to']
    if (session.status !== 'unavailable') {
      await router.replace(typeof to === 'string' && to ? to : '/')
    }
  } finally {
    retrying.value = false
  }
}
</script>
