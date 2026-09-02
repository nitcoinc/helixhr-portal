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
    },
  },
  plugins: [],
}
