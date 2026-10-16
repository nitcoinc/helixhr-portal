<script setup>
// Plan 2026-10-16-001 KTD9: One action-row component for Approvals.
// Fixed order on both phone and desktop: Primary -> Return -> Final No -> Hand-over.
import { computed } from 'vue'
import { Button } from 'frappe-ui'

const props = defineProps({
  kind: { type: String, default: '' },
  actions: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  armed: { type: String, default: '' },
})

const emit = defineEmits(['act'])

const availableActions = computed(() => {
  const allowed = Array.isArray(props.actions) ? props.actions : []
  const list = []

  // 1. Primary action
  if (props.kind === 'change') {
    if (allowed.includes('Accept')) {
      list.push({
        action: 'Accept',
        label: 'Approve change',
        variant: 'solid',
        theme: 'green',
        testid: 'accept',
      })
    }
  } else {
    if (allowed.includes('Approve')) {
      list.push({
        action: 'Approve',
        label: 'Approve',
        variant: 'solid',
        theme: 'green',
        testid: 'approve',
      })
    }
  }

  // 2. Return action (Send back)
  if (allowed.includes('Send Back')) {
    list.push({
      action: 'Send Back',
      label: 'Send back',
      variant: 'outline',
      class: 'decision-tone decision-tone--return',
      testid: 'send-back',
    })
  }

  // 3. Final no (Reject / Decline)
  if (props.kind === 'change') {
    if (allowed.includes('Decline')) {
      list.push({
        action: 'Decline',
        label: 'Decline',
        variant: 'outline',
        class: 'decision-tone decision-tone--final',
        testid: 'decline',
      })
    }
  } else {
    if (allowed.includes('Reject')) {
      list.push({
        action: 'Reject',
        label: 'Reject',
        variant: 'outline',
        class: 'decision-tone decision-tone--final',
        testid: 'reject',
      })
    }
  }

  // 4. Quiet hand-over (Send to HR)
  if (allowed.includes('Send to HR')) {
    list.push({
      action: 'Send to HR',
      label: 'Send to HR',
      variant: 'outline',
      class: 'decision-tone decision-tone--handover',
      testid: 'send-to-hr',
    })
  }

  if (props.armed) {
    return list.filter((item) => item.action === props.armed)
  }
  return list
})
</script>

<template>
  <div
    v-if="availableActions.length"
    class="flex flex-wrap items-center gap-2"
    data-testid="decision-actions"
  >
    <Button
      v-for="btn in availableActions"
      :key="btn.action"
      :variant="btn.variant"
      :theme="btn.theme"
      :class="btn.class"
      :disabled="disabled"
      :loading="loading && armed === btn.action"
      :data-testid="btn.testid"
      @click="emit('act', btn.action)"
    >
      {{ btn.label }}
    </Button>
  </div>
</template>
