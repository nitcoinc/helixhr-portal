<script setup>
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'

// P3-U1 step 7: a stub so the lazy route builds. P3-U2 replaces it.
</script>

<template>
  <div>
    <PageHeader title="Payslips" />
    <AsyncState
      section="payslips"
      :empty="true"
      empty-title="No payslips yet"
      empty-body="Your payslips appear here after the first payroll run."
    />
  </div>
</template>
