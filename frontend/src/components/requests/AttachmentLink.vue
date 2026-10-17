<script setup>
// One request attachment as a pill link that opens the file in a new tab:
// the requester's view (MyRequests) and the handler's (RequestWorkspace).
import Icon from '@/components/Icon.vue'
import { fileSize } from '@/lib/requests'

defineProps({
  /** `{ file_url, file_name, file_size }` from the request's attachments. */
  attachment: { type: Object, required: true },
})
</script>

<template>
  <a
    class="inline-flex min-h-11 items-center gap-2 rounded-full border border-outline-gray-2 px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-2"
    :href="attachment.file_url"
    target="_blank"
    rel="noopener noreferrer"
  >
    <Icon
      name="requests"
      size="h-4 w-4"
      class="text-ink-gray-5"
    />
    {{ attachment.file_name }}
    <span
      v-if="fileSize(attachment.file_size)"
      class="tabular text-ink-gray-5"
    >{{ fileSize(attachment.file_size) }}</span>
  </a>
</template>
