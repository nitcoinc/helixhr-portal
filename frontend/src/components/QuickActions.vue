<script setup>
import { Button } from 'frappe-ui'
</script>

<template>
  <div class="flex flex-wrap gap-2">
    <Button
      label="Ask for leave"
      variant="solid"
      theme="blue"
      route="/leave"
    />
    <Button
      label="Fill timesheet"
      variant="subtle"
      route="/timesheet"
    />
    <Button
      label="New request"
      variant="subtle"
      route="/requests"
    />
  </div>
</template>
