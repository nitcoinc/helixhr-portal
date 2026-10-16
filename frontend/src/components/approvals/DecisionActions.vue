<script setup>
// Plan 2026-10-16-001 KTD9: One action-row component for Approvals, used by
// both the phone and desktop detail. Words and order come from the one kind
// map (`workKinds.KIND_ACTIONS`, KTD6): primary, return, final no, hand-over.
import { computed } from 'vue'
import { Button } from 'frappe-ui'
import { getActionButtons } from '@/lib/workKinds'

const props = defineProps({
  kind: { type: String, default: '' },
  actions: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  /** While a reason or note surface is open, only its own action shows. */
  armed: { type: String, default: '' },
  /** The kind's amount-bearing Approve label, e.g. "Approve 3 days". */
  approveLabel: { type: String, default: '' },
})

const emit = defineEmits(['act'])

// The decision tones the page already styles; the primary is the solid green.
const TONE = {
  'Send Back': 'decision-tone decision-tone--return',
  Reject: 'decision-tone decision-tone--final',
  Decline: 'decision-tone decision-tone--final',
  'Send to HR': 'decision-tone decision-tone--handover',
}

const buttons = computed(() =>
  getActionButtons(props.kind, props.actions)
    .filter((item) => !props.armed || item.action === props.armed)
    .map((item) => ({
      ...item,
      label: item.action === 'Approve' && props.approveLabel ? props.approveLabel : item.label,
      testid: item.action.toLowerCase().replace(/ /g, '-'),
    })),
)
</script>

<template>
  <div
    v-if="buttons.length"
    class="flex flex-wrap items-center gap-2"
    data-testid="decision-actions"
  >
    <Button
      v-for="btn in buttons"
      :key="btn.action"
      :variant="btn.variant === 'solid' ? 'solid' : 'outline'"
      :theme="btn.variant === 'solid' ? 'green' : undefined"
      :class="TONE[btn.action]"
      :disabled="disabled"
      :loading="loading && btn.variant === 'solid'"
      :data-testid="btn.testid"
      @click="emit('act', btn.action)"
    >
      {{ btn.label }}
    </Button>
  </div>
</template>
