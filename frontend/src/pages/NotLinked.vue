<template>
  <div class="flex min-h-screen items-center justify-center bg-surface-white px-4">
    <div class="max-w-sm text-center">
      <h1 class="font-heading text-xl font-semibold text-ink-gray-9">
        Your account is not set up
      </h1>
      <p class="mt-2 text-ink-gray-6">
        We could not find an active employee record for your sign-in.
        Contact HR to get set up.
      </p>
      <a
        :href="`mailto:${hrContactEmail}`"
        class="mt-6 inline-block cursor-pointer text-blue-700 hover:underline"
      >
        Contact HR
      </a>
    </div>
  </div>
</template>

<script setup>
// TODO(runbook): set the real HR contact address once known. Placeholder
// uses the company domain seen elsewhere in this repo so the link at
// least resolves to the right organization in the meantime.
const hrContactEmail = 'hr@nitcoinc.com'
</script>
