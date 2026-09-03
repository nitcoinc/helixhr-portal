module.exports = {
  presets: [require('frappe-ui/tailwind')],
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
    './node_modules/frappe-ui/src/components/**/*.{vue,js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      // Lexend headings, Source Sans 3 body — see docs/design-system.md.
      // frappe-ui's own base styles put "InterVar" first in the font
      // stack; since we don't ship Inter, the browser falls through to
      // fontFamily.sans below.
      fontFamily: {
        heading: ['Lexend', 'sans-serif'],
        sans: ['Source Sans 3', 'sans-serif'],
      },
      colors: {
        blue: {
          // frappe-ui's solid blue Button is the one place that reaches for
          // a raw `bg-blue-500` instead of a semantic token, and white on
          // frappe-ui's blue-500 (#0289F7) measures 3.54:1 -- every primary
          // action in the portal failed WCAG AA. #0070CC (their blue-700
          // value) is the first step that clears 4.5 at 5.01:1. Retuned here
          // rather than at the call site because Button owns that class;
          // nothing else in frappe-ui or this app uses blue-500.
          500: '#0070CC',
        },
      },
    },
  },
  plugins: [],
}
