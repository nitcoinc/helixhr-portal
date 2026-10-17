import { getCurrentScope, onScopeDispose, ref } from 'vue'

// Plan 2026-10-17-001 U3 (R17). The one debounced search: a keystroke waits
// `delay` ms for the next one before the search runs, and only the newest
// query may write `results` -- a slow answer to "pat" that lands after the
// answer to "patel" is dropped, not shown. Every keystroke takes a new
// sequence token, so even a response already in flight for an older query
// is refused once the person has typed again.
//
// `createDebouncedSearch` is the whole behaviour with no component around it,
// so it is testable on its own; `useDebouncedSearch` adds the cleanup a
// component (or any effect scope) wants: the pending timer is cancelled and
// late responses are ignored once the scope is disposed.

export const SEARCH_DELAY_MS = 250

/** `lookup(query)` returns the results (or a promise of them). Returns
 * `search(query)` to call on every keystroke, `cancel()`, and refs for
 * `results`, `loading` and `error`. `loading` is true from the keystroke,
 * not from the request, so a list can say "Loading" instead of showing the
 * previous query's answers while the delay runs. */
export function createDebouncedSearch(lookup, { delay = SEARCH_DELAY_MS, initial = null } = {}) {
  const results = ref(initial)
  const loading = ref(false)
  const error = ref(null)
  let timer = null
  let token = 0

  async function run(query, mine) {
    loading.value = true
    try {
      const value = await lookup(query)
      if (mine !== token) return
      results.value = value
      error.value = null
    } catch (failure) {
      if (mine === token) error.value = failure
    } finally {
      if (mine === token) loading.value = false
    }
  }

  function clearTimer() {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  /** Schedules `query`, replacing whatever was waiting. `wait` overrides
   * the delay for one call -- 0 for a lookup that should not wait, such as
   * the list a picker shows on focus. Only a number counts: wired as
   * `watch(ref, search)`, Vue passes the old value second, and a string
   * there must not cancel the debounce. */
  function search(query, wait) {
    clearTimer()
    const mine = ++token
    loading.value = true
    timer = setTimeout(
      () => {
        timer = null
        run(query, mine)
      },
      typeof wait === 'number' ? wait : delay,
    )
  }

  /** Drops the waiting query and any answer still in flight. */
  function cancel() {
    clearTimer()
    token += 1
    loading.value = false
  }

  return { search, cancel, results, loading, error }
}

/** `createDebouncedSearch`, cancelled when the calling component unmounts
 * (or the surrounding effect scope is disposed). */
export function useDebouncedSearch(lookup, options) {
  const debounced = createDebouncedSearch(lookup, options)
  if (getCurrentScope()) onScopeDispose(debounced.cancel)
  return debounced
}
