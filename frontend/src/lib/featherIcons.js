// P2-U9 step 1. A stand-in for the `feather-icons` package, aliased in
// `vite.config.js`.
//
// Why this exists: frappe-ui's `FeatherIcon.vue` does
// `import feather from 'feather-icons'` and reads `feather.icons`, and both
// `Button.vue` and `Dialog.vue` import that component unconditionally. The
// real package is a single 159KB module containing every Feather glyph, and
// Rollup cannot tree-shake it -- so it landed in the eagerly loaded `Button`
// chunk and cost 96,010 bytes of the 361,976-byte initial JavaScript
// measured after P2-U3. That is 26% of the initial payload for glyphs this
// portal never asks for: every icon on screen comes from
// `src/components/Icon.vue` and the inlined Lucide paths in `src/lib/icons.js`
// (the design system's own rule), and nothing in `src/` passes `icon`,
// `iconLeft` or `iconRight` to a frappe-ui component.
//
// `src/lib/featherIcons.test.js` is the guard: it fails if any component
// starts passing one of those props, at which point either add the glyph to
// `src/lib/icons.js` and use `Icon.vue`, or drop this alias and take the
// 96KB back deliberately.
//
// The fallback below is not decorative. `FeatherIcon.render()` does
// `feather.icons[name] || feather.icons['circle']` and then reads
// `icon.attrs` -- so an empty object would throw rather than degrade. Every
// lookup answers with the same neutral circle instead.
const circle = {
  attrs: {
    xmlns: 'http://www.w3.org/2000/svg',
    width: 24,
    height: 24,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': 2,
    'stroke-linecap': 'round',
    'stroke-linejoin': 'round',
    class: 'feather feather-circle',
  },
  contents: '<circle cx="12" cy="12" r="10"></circle>',
}

// A Proxy rather than a populated object: `Object.keys()` still answers []
// (frappe-ui only uses that in a dev-only prop validator), while any named
// lookup answers the fallback.
export const icons = new Proxy(
  {},
  {
    get: (target, name) => (typeof name === 'string' ? circle : undefined),
    has: () => false,
    ownKeys: () => [],
  },
)

export default { icons }
