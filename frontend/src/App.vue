<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/AppShell.vue'

const route = useRoute()

// NotLinked is the one route that renders without nav chrome (design
// system, "Not linked / login states": centered single-column message
// page, no nav chrome) -- there is nowhere for that user to navigate to.
const withShell = computed(() => route.meta.shell !== false)
</script>

<template>
  <AppShell v-if="withShell">
    <router-view />
  </AppShell>
  <router-view v-else />
</template>
