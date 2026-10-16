<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  ToastClose,
  ToastDescription,
  ToastProvider,
  ToastRoot,
  ToastTitle,
  ToastViewport,
} from 'reka-ui'
import Icon from '@/components/Icon.vue'
import { dismiss, toasts } from '@/lib/notify'

// Plan 2026-10-17-001 U14 (R18, KTD1, KTD4). Renders the `lib/notify.js`
// queue on reka-ui's Toast primitives. Never imported statically: AppShell
// loads this file through `defineAsyncComponent` on idle, so reka's Toast
// modules stay out of the eager chunk (`uiComponents.test.js` guards it).
//
// Not frappe-ui's Toast (KTD1): it never passes `type`, so every message is
// assertive, and its viewport is pinned over the phone tab bar.
//
// Announcing. Two live regions, one polite and one assertive, are in the DOM
// from mount, empty, and each message is written into them after that -- a
// region that arrives already holding its text is often not read at all.
// reka's own announcer is parked in a `hidden` box below: it is a fresh
// `role=alert` node per toast (an alert event even for a polite message),
// and it would sit inside this component's markup, which a modal dialog
// marks aria-hidden. The two regions here survive a modal on purpose:
// reka-ui hides the rest of the page with the `aria-hidden` package, and
// that package leaves every `[aria-live]` element and its ancestors exposed.
// So a toast raised while a bottom sheet is open is still spoken.
//
// Dismissing while a sheet is open. The viewport is a reka
// DismissableLayerBranch, so a tap on a toast is not an "outside" tap and the
// sheet stays open. The sheet's modal layer sets `pointer-events: none` on
// <body>; `.toast` turns it back on. Escape belongs to the sheet: reka closes
// every toast on any Escape, so a toast only obeys Escape while focus is
// inside it.
//
// The toast list carries `aria-live="off"` for the same package rule: without
// it the modal marked the list aria-hidden, so a screen reader (and any
// role-based query) could not find the Dismiss button of a toast raised over
// a sheet. "off" is also the truth -- the list is not what announces; the two
// regions above are. The sheet's focus trap still holds Tab; a pointer or a
// screen reader's virtual cursor reaches the toast, and Tab or F8 does once
// the sheet closes.

const SUCCESS_MS = 4000

const clearance = ref(0)
const ready = ref(false)
/** The message each live region currently holds, by tone. */
const spoken = reactive({ success: null, error: null })
const announced = new Set()

function announcePending() {
  for (const toast of toasts) {
    if (announced.has(toast.id)) continue
    announced.add(toast.id)
    spoken[toast.tone] = toast
  }
}

watch(
  () => toasts.map((toast) => toast.id),
  (ids) => {
    for (const tone of ['success', 'error']) {
      if (spoken[tone] && !ids.includes(spoken[tone].id)) spoken[tone] = null
    }
    if (ready.value) announcePending()
  },
)

let escapedOutside = false
/** reka closes every open toast on any Escape keydown, sheet or not. The
 * close it starts runs synchronously after this, so the flag only has to
 * live until the end of the current task. */
function onEscape() {
  escapedOutside = !document.activeElement?.closest?.('.toast-viewport')
  queueMicrotask(() => {
    escapedOutside = false
  })
}
function onOpenChange(id, open) {
  if (open) return
  if (escapedOutside) {
    escapedOutside = false
    return
  }
  dismiss(id)
}

// Above the phone tab bar and any `.action-bar`, wherever those sit right
// now: an action bar is sticky, so on a short page it rides higher than its
// `bottom` offset. Measured only while a toast is showing.
const CLEAR = '[data-toast-clear], .action-bar'
let frame = 0
let observer = null
function measure() {
  frame = 0
  const height = window.innerHeight
  let top = height
  for (const element of document.querySelectorAll(CLEAR)) {
    const box = element.getBoundingClientRect()
    if (box.height > 0 && box.top < height && box.bottom > 0) top = Math.min(top, box.top)
  }
  clearance.value = Math.ceil(height - top) + 8
}
function scheduleMeasure() {
  if (!frame) frame = requestAnimationFrame(measure)
}
function watchLayout() {
  measure()
  window.addEventListener('resize', scheduleMeasure)
  window.addEventListener('scroll', scheduleMeasure, { passive: true })
  observer = new MutationObserver(scheduleMeasure)
  observer.observe(document.body, { childList: true, subtree: true })
}
function unwatchLayout() {
  window.removeEventListener('resize', scheduleMeasure)
  window.removeEventListener('scroll', scheduleMeasure)
  observer?.disconnect()
  observer = null
  cancelAnimationFrame(frame)
  frame = 0
}
watch(
  () => toasts.length > 0,
  (showing) => (showing ? watchLayout() : unwatchLayout()),
  { immediate: true },
)

onMounted(() => {
  // Two frames, so the empty regions are in the accessibility tree before
  // the first message lands -- including one queued before this mounted.
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      ready.value = true
      announcePending()
    }),
  )
})
onBeforeUnmount(unwatchLayout)
</script>

<template>
  <Teleport to="body">
    <div data-testid="toaster">
      <div
        class="sr-only"
        aria-live="polite"
        aria-atomic="true"
        data-toast-live="polite"
      >
        <p
          v-if="ready && spoken.success"
          :key="spoken.success.id"
        >
          {{ spoken.success.text }} {{ spoken.success.detail }}
        </p>
      </div>
      <div
        class="sr-only"
        aria-live="assertive"
        aria-atomic="true"
        data-toast-live="assertive"
      >
        <p
          v-if="ready && spoken.error"
          :key="spoken.error.id"
        >
          {{ spoken.error.text }} {{ spoken.error.detail }}
        </p>
      </div>

      <ToastProvider
        label="Message"
        swipe-direction="right"
      >
        <!-- reka's per-toast announcer renders here, out of the
             accessibility tree; the toast itself teleports into the
             viewport. -->
        <div hidden>
          <ToastRoot
            v-for="toast in toasts"
            :key="toast.id"
            :open="true"
            :type="toast.tone === 'error' ? 'foreground' : 'background'"
            :duration="toast.tone === 'error' ? Infinity : SUCCESS_MS"
            class="toast elev-2"
            :class="toast.tone === 'error' ? 'surface-alert' : 'surface-card text-ink-gray-9'"
            :data-tone="toast.tone"
            data-testid="toast"
            @escape-key-down="onEscape"
            @update:open="onOpenChange(toast.id, $event)"
          >
            <div class="min-w-0 flex-1 py-1.5">
              <ToastTitle class="text-sm font-medium">
                {{ toast.text }}
              </ToastTitle>
              <ToastDescription
                v-if="toast.detail"
                class="mt-0.5 text-sm"
              >
                {{ toast.detail }}
              </ToastDescription>
            </div>
            <ToastClose
              class="toast-close"
              aria-label="Dismiss"
            >
              <Icon
                name="close"
                size="h-4 w-4"
              />
            </ToastClose>
          </ToastRoot>
        </div>

        <ToastViewport
          label="Messages ({hotkey})"
          aria-live="off"
          class="toast-viewport"
          :style="{ '--toast-clearance': `${clearance}px` }"
        />
      </ToastProvider>
    </div>
  </Teleport>
</template>

<style>
/* Plain (unscoped) rules: the toasts are teleported out of this component's
   tree, so scoped attributes would not reach every element. Prefixed
   `toast-` to stay out of everyone else's way. */
.toast-viewport {
  position: fixed;
  /* Above the tab bar (z-10) and the dialog overlay (50), so a toast raised
     over an open sheet is visible and can be tapped. */
  z-index: 60;
  right: 1rem;
  left: 1rem;
  bottom: max(calc(1rem + env(safe-area-inset-bottom)), var(--toast-clearance, 0px));
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
  outline: none;
}

@media (min-width: 640px) {
  .toast-viewport {
    left: auto;
    width: 22rem;
  }
}

/* A phone sheet owns the bottom edge, and its primary action lives there.
   While one is open the toast drops in from the top instead. */
@media (max-width: 767px) {
  body:has(.dialog-overlay) .toast-viewport {
    top: calc(0.5rem + env(safe-area-inset-top));
    bottom: auto;
  }
}

.toast {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.5rem 0.5rem 1rem;
}

.toast[data-state='open'] {
  animation: toast-in 200ms ease-out;
}

.toast[data-swipe='move'] {
  transform: translateX(var(--reka-toast-swipe-move-x));
}

.toast[data-swipe='cancel'] {
  transform: translateX(0);
  transition: transform 200ms ease-out;
}

@media (prefers-reduced-motion: reduce) {
  .toast[data-state='open'] {
    animation: none;
  }
}

@keyframes toast-in {
  from {
    opacity: 0;
    transform: translateY(0.5rem);
  }
}

.toast-close {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 6px;
  cursor: pointer;
  color: inherit;
}

.toast-close:hover {
  background-color: var(--surface-gray-2);
}

.toast[data-tone='error'] .toast-close:hover {
  background-color: #eccfc6;
}

/* 44 by 44 under a thumb; index.css already lifts the height. */
@media (pointer: coarse) {
  .toast-close {
    width: 44px;
    height: 44px;
  }
}
</style>
