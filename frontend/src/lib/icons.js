// Lucide icon path data, inlined.
//
// The design system says "SVG only (Lucide, matching frappe-ui's own icon
// set)". frappe-ui doesn't re-export Lucide as components, and pulling in
// `lucide-vue-next` for nine glyphs is a dependency we don't need -- these
// are the raw `d` attributes from Lucide's own 24x24 outline set, rendered
// by `Icon.vue`. Add a new entry here rather than pasting an <svg> inline
// in a component, so every icon keeps the same stroke weight and viewbox.
export const icons = {
  home: ['M3 9.5 12 3l9 6.5', 'M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V10'],
  leave: [
    'M8 2v4',
    'M16 2v4',
    'M3 10h18',
    'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
  ],
  attendance: [
    'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
    'M8 2v4',
    'M16 2v4',
    'M3 10h18',
    'm9 16 2 2 4-4',
  ],
  timesheet: ['M12 6v6l4 2', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z'],
  requests: [
    'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z',
    'M14 2v6h6',
    'M9 13h6',
    'M9 17h4',
  ],
  documents: ['M4 6a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z'],
  notifications: ['M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'],
  approvals: ['m9 12 2 2 4-4', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z'],
  profile: ['M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2', 'M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z'],
  more: ['M4 12h16', 'M4 6h16', 'M4 18h16'],
  signOut: ['M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4', 'm16 17 5-5-5-5', 'M21 12H9'],
  chevronLeft: ['m15 18-6-6 6-6'],
  chevronRight: ['m9 18 6-6-6-6'],
}

// Every kind `helixhr.api._get_needs_you` emits, and the glyph its row draws.
// It lives here, next to the glyphs, because `<script setup>` cannot export
// and this map has to be assertable: a kind with no entry falls back silently,
// which is how a sent-back *leave* rendered with the Requests icon for a whole
// unit. `icons.test.js` holds the six kinds against the server's own list.
export const NEEDS_YOU_ICON = {
  timesheet_rejected: 'timesheet',
  leave_rejected: 'leave',
  request_answered: 'requests',
  approval_leave: 'approvals',
  approval_timesheet: 'approvals',
  leave_waiting: 'leave',
}
