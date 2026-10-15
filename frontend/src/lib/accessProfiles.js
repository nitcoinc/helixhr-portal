// Plan 2026-10-15-001 R10: quick-fill profiles for the People & access
// screen. Each profile is a template over role and grant toggles; the
// server validates the resulting toggle set, never the profile name (KTD8).
//
// Leadership and Reports across companies are mutually exclusive (both set
// the same grant field). A role chip adds its roles; un-toggling it removes
// only those roles. `matchProfiles(toggles)` returns the matching profiles
// or ["Custom"] when none match.

const PROFILES = [
  {
    key: 'employee',
    label: 'Employee',
    roles: [],
    grant: null,
  },
  {
    key: 'delivery-manager',
    label: 'Delivery Manager',
    roles: ['HelixHR Delivery Manager'],
    grant: null,
  },
  {
    key: 'it',
    label: 'IT',
    roles: ['IT Team'],
    grant: null,
  },
  {
    key: 'reports',
    label: 'Report Manager',
    roles: ['HelixHR Report Manager'],
    grant: null,
  },
  {
    key: 'notifications',
    label: 'Notification Manager',
    roles: ['HelixHR Notification Manager'],
    grant: null,
  },
  {
    key: 'leadership',
    label: 'Leadership (All data grant)',
    roles: [],
    grant: { level: 'All data' },
  },
  {
    key: 'cross-company-reports',
    label: 'Reports across companies (Reports only grant)',
    roles: [],
    grant: { level: 'Reports only' },
  },
  // HR profiles: shown only to System Manager.
  {
    key: 'hr-user',
    label: 'HR User',
    roles: ['HR User'],
    grant: null,
  },
  {
    key: 'hr-manager',
    label: 'HR Manager',
    roles: ['HR Manager'],
    grant: null,
  },
]

/** Which profiles apply to a set of roles and grant level. */
export function matchProfiles(roles, grantLevel) {
  const activeRoles = new Set(roles || [])
  const matches = PROFILES.filter((profile) => {
    const roleMatch = profile.roles.every((role) => activeRoles.has(role)) && profile.roles.length > 0
    const grantMatch =
      profile.grant && grantLevel === profile.grant.level
    return roleMatch || grantMatch
  })
  const emptyPro = PROFILES.find((p) => p.key === 'employee')
  if (matches.length === 0 && emptyPro) {
    const hasNoRoles = !roles || roles.length === 0
    const hasNoGrant = !grantLevel
    if (hasNoRoles && hasNoGrant) return [emptyPro]
    return [{ key: 'custom', label: 'Custom', roles: roles || [], grant: grantLevel ? { level: grantLevel } : null }]
  }
  return matches
}

export function getProfiles(isSystemManager) {
  if (isSystemManager) return PROFILES
  return PROFILES.filter((p) => p.key !== 'hr-user' && p.key !== 'hr-manager')
}