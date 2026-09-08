<script setup>
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'

// P3-U1 step 7: a stub so the lazy route builds. P3-U3 replaces it.
</script>

<template>
  <div>
    <PageHeader title="Holidays" />
    <AsyncState
      section="holidays"
      :empty="true"
      empty-title="No holidays to show yet"
      empty-body="Your holiday list appears here once HR assigns one."
    />
  </div>
</template>
