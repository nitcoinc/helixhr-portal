<script setup>
import { ref, watch } from 'vue'

// Plan 2026-09-30-001 U5 / KTD6. The one avatar every surface draws: the
// employee's photo when the server projected a `photo_url`, the initials
// monogram otherwise. The URL is `helixhr.api.get_employee_photo`, which
// answers an error (no bytes) when there is no photo or the viewer may not
// see it -- so a failed load is the normal "no photo" path, not a fault, and
// it falls back to exactly the monogram the surface drew before photos.
//
// The caller's `class` carries the size and the monogram's colours, so a
// surface without a photo renders the same markup it always did.
const props = defineProps({
  photoUrl: { type: String, default: null },
  initials: { type: String, default: '' },
  /** Rendered edge in px. Fixed `width`/`height` on the img, so a photo
   * arriving late shifts nothing (no CLS). */
  size: { type: Number, required: true },
  /** The image's alt text. Leave it empty (the default) wherever the
   * person's name is printed next to the avatar: the photo is then
   * decorative (alt="" + aria-hidden) so a screen reader does not read the
   * name twice. Pass the name only where the avatar stands alone. */
  name: { type: String, default: '' },
})

const failed = ref(false)
// A new URL (replaced photo, a different row reusing the component) gets a
// fresh chance to load.
watch(
  () => props.photoUrl,
  () => {
    failed.value = false
  },
)
</script>

<template>
  <img
    v-if="photoUrl && !failed"
    :src="photoUrl"
    :alt="name"
    :aria-hidden="name ? undefined : 'true'"
    :width="size"
    :height="size"
    loading="lazy"
    decoding="async"
    class="shrink-0 rounded-full object-cover"
    data-testid="avatar-img"
    @error="failed = true"
  >
  <span
    v-else
    aria-hidden="true"
    data-testid="avatar-initials"
  ><slot>{{ initials }}</slot></span>
</template>
