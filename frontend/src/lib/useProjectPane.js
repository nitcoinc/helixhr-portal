// Plan 2026-10-31-001 U3 (R3, R4). One project pane's search and "show
// more" window: `get_project` already returns every open task and member
// (KTD3), so both panes reveal 25 matching rows at a time.
import { computed, ref, watch } from 'vue'
import { matchRows } from './projects'

const PANE_PAGE = 25

/** Search `source` (a ref of rows) on `field`, showing a page at a time. */
export function useProjectPane(source, field) {
  const query = ref('')
  const limit = ref(PANE_PAGE)
  watch(query, () => (limit.value = PANE_PAGE))
  const matched = computed(() => matchRows(source.value, query.value, field))
  const shown = computed(() => matched.value.slice(0, limit.value))
  const more = computed(() => Math.min(PANE_PAGE, matched.value.length - shown.value.length))
  function showMore() {
    limit.value += PANE_PAGE
  }
  function reset() {
    query.value = ''
    limit.value = PANE_PAGE
  }
  /** The pane's count line, e.g. "3 of 40 tasks match." or "Showing 25 of
   * 40 tasks."; `noun` is already singular or plural for the total. */
  function countLine(noun) {
    const total = source.value.length
    if (!total) return ''
    if (query.value.trim()) return `${matched.value.length} of ${total} ${noun} match.`
    return shown.value.length < total ? `Showing ${shown.value.length} of ${total} ${noun}.` : ''
  }
  return { query, matched, shown, more, showMore, reset, countLine }
}
