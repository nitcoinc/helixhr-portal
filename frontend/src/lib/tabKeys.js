// Plan 2026-10-17-001 U3. The keyboard model every tab strip shares: the
// arrow keys move one tab and wrap at the ends, Home and End jump to the
// ends (WAI-ARIA tabs pattern). Pulled out of TabBar.vue so it can be tested
// without mounting a component -- a `<script setup>` cannot export.

/** The tab index `key` moves to from `index` in a strip of `count` tabs, or
 * -1 when `key` is not one the strip handles. An `index` of -1 (nothing
 * focused or selected yet) treats ArrowRight as "the first" and ArrowLeft as
 * "the last". */
export function tabIndexForKey(key, index, count) {
  if (count < 1) return -1
  const from = index >= 0 && index < count ? index : -1
  switch (key) {
    case 'ArrowRight':
      return from < 0 ? 0 : (from + 1) % count
    case 'ArrowLeft':
      return from < 0 ? count - 1 : (from - 1 + count) % count
    case 'Home':
      return 0
    case 'End':
      return count - 1
    default:
      return -1
  }
}
