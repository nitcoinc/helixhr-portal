import { reactive } from 'vue'

// Plan 2026-10-17-001 KTD8. One shared pending-decisions count, the way
// lib/unread.js keeps the unread one: the rail's Approvals item, the More
// row and the Approvals tab (or the More tab) all read it.
//
// It is never polled. The bootstrap seeds it once per hard load from a
// count-only run of the Approvals page's own read; the Approvals page sets it
// from `get_my_approvals.total` every time it loads; a decision takes one off
// locally. New arrivals show at the next hard load or Approvals visit -- the
// accepted staleness.
const state = reactive({ count: 0, capped: false })

/** Set the count from the bootstrap's `approvals_count` /
 * `approvals_count_capped`, or from an answer the Approvals page just got
 * (`total`, `total_is_capped`). `capped` means the count is a floor: "N+". */
export function setApprovalsCount(count, capped = false) {
  state.count = Math.max(0, Number(count) || 0)
  state.capped = !!capped
}

/** Take `by` decisions off after they were recorded, before the page's own
 * reload answers with the new total. A floor stays a floor. */
export function decrementApprovals(by = 1) {
  setApprovalsCount(state.count - (Number(by) || 0), state.capped)
}

/** The badge text: "12", or "150+" when the count is a floor; '' at zero. */
export function approvalsLabel() {
  if (state.count <= 0) return ''
  return `${state.count}${state.capped ? '+' : ''}`
}
