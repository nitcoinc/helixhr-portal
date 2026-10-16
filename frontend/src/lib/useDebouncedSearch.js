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
 * `results`, `loading` and `error`. */
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

  /** Schedules `query`, replacing whatever was waiting. */
  function search(query) {
    clearTimer()
    const mine = ++token
    timer = setTimeout(() => {
      timer = null
      run(query, mine)
    }, delay)
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
