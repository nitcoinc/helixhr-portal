/**
 * Plan 2026-10-16-001 KTD6: One work kind map.
 *
 * Holds each kind's tag (word and icon) and action buttons mapped to server
 * action names, plus reason copy.
 */

export const KIND_TAGS = {
  timesheet: { label: 'Timesheet', icon: 'timesheet' },
  change: { label: 'Timesheet change', icon: 'timesheetChange' },
  leave: { label: 'Leave', icon: 'leave' },
  attendance: { label: 'Attendance', icon: 'attendance' },
  request_hr: { label: 'HR request', icon: 'requests' },
  request_it: { label: 'IT request', icon: 'itRequest' },
  // Plan 2026-10-31-001 U10: a manager's roster request, decided by HR.
  roster: { label: 'Roster change', icon: 'calendarClock' },
}

export function getKindTag(kindOrRow, routedToRole = null) {
  let kind = kindOrRow
  let role = routedToRole
  if (typeof kindOrRow === 'object' && kindOrRow !== null) {
    kind = kindOrRow.kind || kindOrRow.route_kind || ''
    role = kindOrRow.routed_to_role || kindOrRow.routed_role || null
  }
  if (kind === 'request') {
    return role === 'IT Team' ? KIND_TAGS.request_it : KIND_TAGS.request_hr
  }
  if (kind === 'change') {
    return KIND_TAGS.change
  }
  if (kind === 'timesheet') {
    return KIND_TAGS.timesheet
  }
  if (kind === 'leave') {
    return KIND_TAGS.leave
  }
  if (kind === 'attendance') {
    return KIND_TAGS.attendance
  }
  if (kind === 'roster') {
    return KIND_TAGS.roster
  }
  return { label: kind || '—', icon: 'requests' }
}

export const KIND_ACTIONS = {
  timesheet: [
    { action: 'Approve', label: 'Approve', variant: 'solid' },
    { action: 'Send Back', label: 'Send back', variant: 'subtle' },
    { action: 'Send to HR', label: 'Send to HR', variant: 'subtle' },
  ],
  change: [
    { action: 'Accept', label: 'Approve change', variant: 'solid' },
    { action: 'Decline', label: 'Decline', variant: 'subtle' },
  ],
  leave: [
    { action: 'Approve', label: 'Approve', variant: 'solid' },
    { action: 'Reject', label: 'Reject', variant: 'subtle' },
    { action: 'Send to HR', label: 'Send to HR', variant: 'subtle' },
  ],
  attendance: [
    { action: 'Approve', label: 'Approve', variant: 'solid' },
    { action: 'Send Back', label: 'Send back', variant: 'subtle' },
    { action: 'Reject', label: 'Reject', variant: 'subtle' },
    { action: 'Send to HR', label: 'Send to HR', variant: 'subtle' },
  ],
  // Plan 2026-10-31-001 R15: Decline is final, Send back reopens it as the
  // manager's draft; both carry a reason.
  roster: [
    { action: 'Approve', label: 'Approve', variant: 'solid' },
    { action: 'Send Back', label: 'Send back', variant: 'subtle' },
    { action: 'Decline', label: 'Decline', variant: 'subtle' },
  ],
  request: [
    { action: 'Pick up', label: 'Assign to me', variant: 'subtle' },
    { action: 'Done', label: 'Mark done', variant: 'solid' },
    { action: 'Reject', label: 'Decline', variant: 'subtle' },
    { action: 'Need info', label: 'Send and wait for reply', variant: 'subtle' },
  ],
}

export function getActionButtons(kind, serverActions = []) {
  const allowed = Array.isArray(serverActions) ? serverActions : []
  const list = KIND_ACTIONS[kind] || []
  return list.filter((item) => allowed.includes(item.action))
}

export const REASON_COPY = {
  'Send Back': {
    heading: 'Send back with a reason',
    placeholder: 'What should they change?',
    missing: 'Say what should change before sending it back.',
  },
  Reject: {
    heading: 'Reject with a reason',
    placeholder: 'Why is the answer final?',
    missing: 'Say why before rejecting this.',
  },
  Decline: {
    heading: 'Decline with a reason',
    placeholder: 'Why is the week staying as it is?',
    missing: 'Say why before declining this.',
  },
  'Need info': {
    heading: 'Ask a question',
    placeholder: 'What do you need from them?',
    missing: 'Say what you need before sending this.',
  },
}

// A kind whose reason reads differently from the shared copy. Decline's
// shared placeholder is the timesheet change's "week".
const KIND_REASON_COPY = {
  roster: {
    Decline: {
      heading: 'Decline with a reason',
      placeholder: 'Why are the shifts staying as they are?',
      missing: 'Say why before declining this.',
    },
  },
}

export function getReasonCopy(action, kind = '') {
  return KIND_REASON_COPY[kind]?.[action] || REASON_COPY[action] || REASON_COPY['Send Back']
}
