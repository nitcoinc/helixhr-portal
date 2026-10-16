import { reactive } from 'vue'

// Plan 2026-10-17-001 U14 (R18, KTD4). The one feedback channel for a write:
// a success is a polite toast that leaves on its own, a failure is announced
// and stays until it is dismissed.
//
// This file is eager and stays tiny on purpose (R4: about 1KB). It holds the
// queue and nothing else -- no reka-ui, no component. `Toaster.vue` is the
// heavy half; AppShell loads it on idle, and a message sent before then waits
// here and shows once it mounts.
//
// A toast never stands in for a confirm step (KTD6): an irreversible action
// keeps its confirm, and the toast only reports the outcome afterwards. No
// "Undo" toast on a workflow transition, which has no reverse path.

/** At most this many messages wait or show at once. Past that the oldest
 * success goes first, so an error is only dropped by a newer error. */
export const MAX_TOASTS = 3

/** `{id, tone: 'success' | 'error', text, detail}`, oldest first. */
export const toasts = reactive([])

let lastId = 0

/** Queue a message. `tone` is 'success' (default) or 'error'. Returns its id. */
export function notify(text, { tone = 'success', detail = '' } = {}) {
  const id = ++lastId
  toasts.push({ id, tone: tone === 'error' ? 'error' : 'success', text: String(text), detail })
  if (toasts.length > MAX_TOASTS) {
    const success = toasts.findIndex((toast) => toast.tone === 'success')
    toasts.splice(success === -1 ? 0 : success, 1)
  }
  return id
}

notify.success = (text) => notify(text)
notify.error = (text, { detail = '' } = {}) => notify(text, { tone: 'error', detail })

/** Remove one message, whether or not it is still showing. */
export function dismiss(id) {
  const index = toasts.findIndex((toast) => toast.id === id)
  if (index !== -1) toasts.splice(index, 1)
}
