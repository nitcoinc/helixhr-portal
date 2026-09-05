// The frappe-ui components this app imports by name. Keep it in sync with
// `src/**` -- the vitest guard next to it does exactly that.
const FRAPPE_UI_IN_USE = ['Button', 'Dialog', 'FormControl']

// What those three render underneath, read off their own imports:
// Dialog -> Button; Button -> Tooltip, LoadingIndicator, FeatherIcon;
// FormControl -> TextInput, Select, Textarea, Checkbox, Autocomplete,
// Combobox, FormLabel; Autocomplete/Combobox -> Popover, ErrorMessage.
const FRAPPE_UI_TRANSITIVE = [
  'Autocomplete',
  'Checkbox',
  'Combobox',
  'ErrorMessage',
  'FeatherIcon.vue',
  'FormLabel.vue',
  'Input.vue',
  'LoadingIndicator.vue',
  'Popover',
  'Select',
  'Textarea',
  'TextInput',
  'Tooltip',
]

module.exports = {
  presets: [require('frappe-ui/tailwind')],
  content: [
    './index.html',
    './src/**/*.{vue,js,ts,jsx,tsx}',
    // P2-U9 / P2-R24. Scanning *every* frappe-ui component generated the
    // utilities for Calendar, Charts, TextEditor, ListView, Tree, the
    // pickers and everything else this portal never renders: 167,821 bytes
    // of CSS against U0's 162,906-byte budget, which R24 forbids
    // regressing past. Scanning only the components that can actually reach
    // the DOM here brings it to 81,080 bytes.
    //
    // Two halves, and they fail differently:
    //   * FRAPPE_UI_IN_USE is what `src/` imports by name.
    //     `src/lib/frappeUiComponents.test.js` fails if an import is added
    //     without adding it here.
    //   * FRAPPE_UI_TRANSITIVE is what those components render underneath
    //     (Dialog and FormControl both compose others). Nothing checks this
    //     one automatically; a gap shows up as an unstyled control the
    //     first time that path renders, which is loud rather than silent.
    ...[...FRAPPE_UI_IN_USE, ...FRAPPE_UI_TRANSITIVE].map((component) =>
      component.endsWith('.vue')
        ? `./node_modules/frappe-ui/src/components/${component}`
        : `./node_modules/frappe-ui/src/components/${component}/**/*.{vue,js,ts,jsx,tsx}`,
    ),
  ],
  theme: {
    extend: {
      // Archivo, one family across every role. Product UI rarely needs a
      // display/body pairing -- it needs one well-cut grotesque with real
      // weight range, so labels, data and headings stay related. The old
      // Lexend/Source Sans 3 pairing set headings and body 4px apart and
      // carried no hierarchy of its own. `heading` is kept as an alias so
      // existing `font-heading` call sites keep working.
      fontFamily: {
        heading: ['Archivo', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['Archivo', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      colors: {
        // ── Signal brand green ──────────────────────────────────────────
        // Read `blue` as "brand" everywhere in this app. frappe-ui's own
        // Button and Badge hard-code `blue` as their primary theme (and
        // Button reaches for a raw `bg-blue-500`), so retuning the scale is
        // the only way to re-brand those components without forking the
        // library. Every step below is measured: white on 500 is 6.08, on
        // 600 (button hover) 7.18, on 800 (the field) 12.03; 700 as text is
        // 8.96 on surface and 7.61 on paper.
        blue: {
          50: '#EAF2EF',
          100: '#D6E7E0',
          200: '#AFCFC3',
          300: '#7FB29F',
          400: '#4C8F79',
          500: '#1E6F53',
          600: '#1A6349',
          700: '#14523F',
          800: '#143D33',
          900: '#0E2C25',
        },
        // The accent, and the one colour with a hard usage rule: it measures
        // 1.21:1 on the paper ground, so it may only ever appear inside the
        // deep field, where it is 8.35:1. On light surfaces the warm accent
        // is `amber-ink` below.
        signal: {
          DEFAULT: '#FFD24A',
          soft: '#FDEFC9',
        },
        paper: '#EFEAE4',
        field: {
          DEFAULT: '#143D33',
          deep: '#0E2C25',
        },
      },
    },
  },
  plugins: [],
}
