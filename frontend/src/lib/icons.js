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
  itRequest: [
    'M20 3H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2z',
    'M8 21h8',
    'M12 17v4',
  ],
  timesheetChange: [
    'M12 20h9',
    'M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z',
  ],
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
  chevronDown: ['m6 9 6 6 6-6'],
  // P3-U1 step 2: Payslips, Holidays, Directory/Team, and the pin a punch
  // with location wears in the day sheet.
  wallet: [
    'M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1',
    'M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4',
  ],
  sun: [
    'M12 2v2',
    'M12 20v2',
    'm4.93 4.93 1.41 1.41',
    'm17.66 17.66 1.41 1.41',
    'M2 12h2',
    'M20 12h2',
    'm6.34 17.66-1.41 1.41',
    'm19.07 4.93-1.41 1.41',
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  ],
  users: [
    'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2',
    'M22 21v-2a4 4 0 0 0-3-3.87',
    'M16 3.13a4 4 0 0 1 0 7.75',
    'M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  ],
  pin: [
    'M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0',
    'M12 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  ],
  // P5-U14: the Settings nav item.
  settings: [
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  ],
  // P5-U15: the Organisation nav item.
  organisation: [
    'M4 21V4a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v17',
    'M15 21v-9a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v9',
    'M4 21h17',
    'M7.5 7h1.5M7.5 11h1.5M7.5 15h1.5M11.5 7h1.5M11.5 11h1.5M11.5 15h1.5',
  ],
  // P6-U5: the People nav item -- a search glyph, distinct from Directory's
  // "users" (a lookup, not a browse).
  peopleSearch: [
    'M11 21a10 10 0 1 0 0-20 10 10 0 0 0 0 20z',
    'm21 21-4.35-4.35',
  ],
  // P6-U6: the Reports nav item.
  reports: [
    'M3 3v18h18',
    'M18 17V9',
    'M13 17V5',
    'M8 17v-3',
  ],
  // P7-U5: the Projects nav item -- a folder glyph, distinct from Directory's
  // "users" and People's "peopleSearch" (this is a work container, not a person).
  folder: [
    'M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z',
  ],
  // Plan 2026-10-17-001 U2: Lucide's own `search`, for the palette's input
  // and the shell's search triggers.
  // `peopleSearch` above is the People nav glyph and stays its own name.
  search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'm21 21-4.3-4.3'],
  // U14: the toast's dismiss control (Lucide `x`).
  close: ['M18 6 6 18', 'm6 6 12 12'],
  // Plan 2026-10-17-001 U4 (MH-21, AI-23, EP-25): one glyph per destination.
  // Lucide's `briefcase` (My projects; Projects keeps the folder), `users-round`
  // (Team; Directory keeps `users`), `calendar-clock` (Roster; Timesheet keeps
  // the clock) and `mail` (Email templates; Notifications keeps the bell).
  // Circles and rects are written as paths, because Icon.vue draws paths.
  briefcase: [
    'M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
    'M4 6h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z',
  ],
  usersRound: [
    'M18 21a8 8 0 0 0-16 0',
    'M10 3a5 5 0 1 0 0 10 5 5 0 0 0 0-10z',
    'M22 20c0-3.37-2-6.5-4-8a5 5 0 0 0-.45-8.3',
  ],
  calendarClock: [
    'M21 7.5V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3.5',
    'M16 2v4',
    'M8 2v4',
    'M3 10h5',
    'M17.5 17.5 16 16.3V14',
    'M16 10a6 6 0 1 0 0 12 6 6 0 0 0 0-12z',
  ],
  mail: [
    'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
    'm22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7',
  ],
  // "Ask HR" (R10): Lucide `message-circle-question`.
  askHr: ['M7.9 20A9 9 0 1 0 4 16.1L2 22z', 'M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3', 'M12 17h.01'],
}
