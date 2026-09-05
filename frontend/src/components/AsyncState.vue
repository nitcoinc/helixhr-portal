<script setup>
import { computed, watch } from 'vue'
import { Button } from 'frappe-ui'

// P2-U3 / P2-R2 / P2-R25. One async region, used by every resource-backed
// part of every page.
//
// It exists because the portal had six different answers to "the data isn't
// here yet". Some pages printed "Loading…" in muted ink; the dashboard drew
// skeletons; several printed nothing at all. Worse, *every* page rendered a
// failed request as its empty state -- `v-else-if="rows.length === 0"` is
// true when a request 500s, so an outage read as "You have no requests yet"
// (P2-AE8). Five states, told apart on purpose:
//
//   pending      the request is in flight       -> a sized skeleton
//   unavailable  the request failed             -> a retry panel, never empty
//   forbidden    the server refused this user   -> its own words, no Retry
//   empty        the request succeeded, no rows -> the task and its next step
//   ready        the request succeeded          -> the default slot
//
// The skeleton is *sized*, and that is the point rather than a detail. The
// U0 baseline measured CLS 0.8431 on a cold Dashboard load: a two-row
// skeleton resolved into an eight-row queue and shoved the whole rest of the
// page down 210px. A region that reserves the room it is going to need, and
// pages that keep every resource-dependent element inside one such region,
// are what brings that back under P2-R23's 0.1.
const props = defineProps({
  /**
   * The frappe-ui resource this region renders: anything exposing
   * `{ loading, error, data, reload() }`. Optional, so a page can drive the
   * region from several resources through `loading` / `error` instead.
   */
  resource: { type: Object, default: null },
  /**
   * Which region this is, in words. Goes into the failure log so P2-R25's
   * "which section failed" question is answerable from a console line, and
   * into `data-async-state` so tests can address the region.
   */
  section: { type: String, required: true },
  /** Override `resource.loading`. */
  loading: { type: Boolean, default: null },
  /** Override `resource.error`. */
  error: { type: [Object, String], default: null },
  /**
   * Whether a successful response resolved to nothing. Always the page's own
   * answer: "empty" is a property of what the page computed from the response,
   * not of the response's shape, and every region in the portal knows it.
   */
  empty: { type: Boolean, required: true },
  /** The empty state's headline. Names the task, never "No data". */
  emptyTitle: { type: String, default: 'Nothing here yet' },
  /** One line under it, naming the next action. */
  emptyBody: { type: String, default: '' },
  /**
   * The shape to reserve while pending.
   *   'card'  stacked resting cards (a list)
   *   'row'   short single lines (a compact list)
   *   'field' one anchored field block
   *   'block' one plain rectangle, `skeletonHeight` tall
   */
  skeleton: { type: String, default: 'card' },
  /** How many skeleton items to draw. Match the page's usual row count. */
  skeletonRows: { type: Number, default: 3 },
  /** Tailwind height class for 'block', and for each 'card'/'row' item. */
  skeletonHeight: { type: String, default: '' },
})

const isLoading = computed(() =>
  props.loading === null ? !!props.resource?.loading : props.loading,
)
const failure = computed(() => props.error || props.resource?.error || null)

// A refusal is not an outage and must not offer Retry -- retrying a
// permission failure just fails again, which is how a portal teaches people
// that its buttons are decorative. Frappe answers both with HTTP 403, so the
// exception type is what separates them.
const isForbidden = computed(() => {
  const error = failure.value
  if (!error) return false
  const type = error.exc_type || ''
  return type === 'PermissionError' || type === 'ValidationError:PermissionError'
})

const state = computed(() => {
  if (isLoading.value) return 'pending'
  if (failure.value) return isForbidden.value ? 'forbidden' : 'unavailable'
  if (props.empty) return 'empty'
  return 'ready'
})

// P2-R25: the failure carries which call produced it (`helixhrMethod`, set in
// lib/api.js) and this adds which region was showing it. Logged once per
// transition into the state, not once per render.
watch(
  () => state.value === 'unavailable' && failure.value,
  (error) => {
    if (!error) return
    console.error(
      `[helixhr:${props.section}] request failed${
        error.helixhrMethod ? ` (${error.helixhrMethod})` : ''
      }`,
      error,
    )
  },
)

const skeletonItemHeight = computed(() => {
  if (props.skeletonHeight) return props.skeletonHeight
  return props.skeleton === 'row' ? 'h-14' : 'h-20'
})

function retry() {
  props.resource?.reload()
}
</script>

<template>
  <div :data-async-state="`${section}:${state}`">
    <!-- Pending. `aria-busy` plus a caption, because a silent pulsing
         rectangle is not a loading message to a screen reader, and under
         `prefers-reduced-motion` the pulse is pinned to a resting tint
         (index.css) so the caption is the only thing left saying so. -->
    <div
      v-if="state === 'pending'"
      aria-busy="true"
      :aria-label="`Loading ${section.replace(/-/g, ' ')}`"
      role="status"
    >
      <slot name="skeleton">
        <div
          v-if="skeleton === 'field'"
          class="animate-pulse rounded-xl bg-field/10"
          :class="skeletonHeight || 'h-40'"
        />
        <div
          v-else-if="skeleton === 'block'"
          class="animate-pulse rounded-lg bg-surface-gray-2"
          :class="skeletonHeight || 'h-24'"
        />
        <div
          v-else
          class="space-y-2"
        >
          <div
            v-for="n in skeletonRows"
            :key="n"
            class="animate-pulse rounded-lg bg-surface-gray-2"
            :class="skeletonItemHeight"
          />
        </div>
      </slot>
    </div>

    <!-- Unavailable. The one state the portal used to render as "you have
         nothing". It says whose fault it is, and offers exactly one bounded
         retry (P2-R25) rather than a reload of the whole app. -->
    <div
      v-else-if="state === 'unavailable'"
      class="surface-alert p-4"
      role="alert"
    >
      <p class="font-medium">
        <slot name="error-title">
          We couldn't load this
        </slot>
      </p>
      <p class="mt-1 text-sm">
        Something went wrong on our side, not with your account.
      </p>
      <Button
        class="mt-3"
        variant="outline"
        :loading="isLoading"
        @click="retry"
      >
        Retry
      </Button>
    </div>

    <!-- Forbidden. No Retry: nothing about trying again changes the answer. -->
    <div
      v-else-if="state === 'forbidden'"
      class="surface-card p-4"
      role="alert"
    >
      <p class="font-medium text-ink-gray-9">
        You don't have access to this
      </p>
      <p class="mt-1 text-sm text-ink-gray-6">
        If you think that's wrong, ask HR to check your access.
      </p>
    </div>

    <!-- Empty, and only ever reached after a *successful* response. Names
         the task and its next step (docs/design-system.md, copy rules). -->
    <div
      v-else-if="state === 'empty'"
      class="surface-card p-5"
    >
      <p class="font-medium text-ink-gray-9">
        {{ emptyTitle }}
      </p>
      <p
        v-if="emptyBody"
        class="mt-1 text-sm text-ink-gray-6"
      >
        {{ emptyBody }}
      </p>
      <div
        v-if="$slots['empty-action']"
        class="mt-3"
      >
        <slot name="empty-action" />
      </div>
    </div>

    <slot v-else />
  </div>
</template>
