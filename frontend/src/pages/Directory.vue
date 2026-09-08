<script setup>
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'

// P3-U1 step 7: a stub so the lazy route builds. P3-U8 replaces it.
</script>

<template>
  <div>
    <PageHeader title="Directory" />
    <AsyncState
      section="directory"
      :empty="true"
      empty-title="The directory is not ready yet"
      empty-body="Colleagues in your company appear here."
    />
  </div>
</template>
