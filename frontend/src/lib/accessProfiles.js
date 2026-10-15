// Plan 2026-10-15-001 R10: quick-fill profiles for the People & access
// screen. Each profile is a template over role and grant toggles; the
// server validates the resulting toggle set, never the profile name (KTD8).
//
// Leadership and Reports across companies are mutually exclusive (both set
// the same grant field). A role chip adds its roles; un-toggling it removes
// only those roles. `matchProfiles(roles, grantLevel)` returns the matching
// profiles, or Custom when they do not describe the person exactly.

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

const CUSTOM = { key: 'custom', label: 'Custom', roles: [], grant: null }

/** The profiles that exactly describe `roles` (managed and HR roles held)
 * and `grantLevel`: every held role is covered by a matching role profile
 * and the grant level by its grant profile. Anything left over -- a role
 * no profile names, say -- makes the person Custom; nothing at all makes
 * them an Employee. */
export function matchProfiles(roles, grantLevel) {
  const held = new Set(roles || [])
  const matches = PROFILES.filter((profile) => profile.roles.length && profile.roles.every((role) => held.has(role)))
  const covered = new Set(matches.flatMap((profile) => profile.roles))
  const grantProfile = grantLevel ? PROFILES.find((profile) => profile.grant?.level === grantLevel) : null
  if ([...held].some((role) => !covered.has(role)) || (grantLevel && !grantProfile)) return [CUSTOM]
  if (grantProfile) matches.push(grantProfile)
  return matches.length ? matches : PROFILES.filter((profile) => profile.key === 'employee')
}

export function getProfiles(isSystemManager) {
  if (isSystemManager) return PROFILES
  return PROFILES.filter((p) => p.key !== 'hr-user' && p.key !== 'hr-manager')
}