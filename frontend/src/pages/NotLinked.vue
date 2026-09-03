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
        v-if="hrContactEmail"
        :href="`mailto:${hrContactEmail}`"
        class="mt-6 inline-block cursor-pointer text-blue-700 hover:underline"
      >
        {{ hrContactEmail }}
      </a>
    </div>
  </div>
</template>

<script setup>
// Site configuration, not a constant: helixhr/www/helixhr.py injects the
// `helixhr_hr_contact` site-config key as a window global when it serves
// the shell. Unset means the page says "Contact HR" with no link rather
// than pointing at a made-up address.
const hrContactEmail = window.helixhr_hr_contact || ''
</script>
