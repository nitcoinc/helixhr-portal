<script setup>
import PageHeader from '@/components/PageHeader.vue'
import AsyncState from '@/components/AsyncState.vue'

// P3-U1 step 7: a stub so the lazy route builds. P3-U7 replaces it.
</script>

<template>
  <div>
    <PageHeader title="Team" />
    <AsyncState
      section="team"
      :empty="true"
      empty-title="Nothing to show for your team yet"
      empty-body="Who is out this week appears here."
    />
  </div>
</template>
