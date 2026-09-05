import { onMounted, onUnmounted, ref } from 'vue'

// One breakpoint decides the *shape*, never the identity: the URL is the same
// at both widths (KTD5). 1024px is where the shell drops the phone tab bar for
// the side nav, so it is also where there is room for a list and a record side
// by side.
//
// Leave, Approvals and Requests each carried a byte-identical copy of this,
// literal included. It is behaviour, not presentation, so it lives here rather
// than becoming a shared UI primitive (P2-R9).
const DESKTOP = '(min-width: 1024px)'

/** `true` while the viewport is at desktop width. Starts `false`, so a first
 * render before mount is the phone shape rather than a guess. */
export function useIsDesktop() {
  const isDesktop = ref(false)
  let query = null
  function sync(event) {
    isDesktop.value = event.matches
  }
  onMounted(() => {
    query = window.matchMedia(DESKTOP)
    isDesktop.value = query.matches
    query.addEventListener('change', sync)
  })
  onUnmounted(() => query?.removeEventListener('change', sync))
  return isDesktop
}
