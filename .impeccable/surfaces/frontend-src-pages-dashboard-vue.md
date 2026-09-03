---
version: 1
slug: "frontend-src-pages-dashboard-vue"
primary_target: "frontend/src/pages/Dashboard.vue"
related_targets: ["helixhr/api.py"]
---

Scope: the portal home screen (`/helixhr`). Visitor mode: Operate.

Audience: an employee opening the portal daily to act on whatever is waiting for
them, not to browse numbers. Mobile-first. Managers see the same screen plus
their approvals. Constraint from the ask round: nothing on the old screen is
sacred, including which data earns a place.

## Direction contract

THESIS: the working week is the page. Refuses the six same-size stat cards the
old screen shipped, where a sent-back timesheet and a leave balance carried
identical weight.

OWN-WORLD: unchanged and inherited — frappe-ui components, Lexend headings on
Source Sans 3, blue-700 accent, gray-50 ground, 1px outline-gray-2 cards. No new
visual language; this round decides arrangement only.

STORY: the employee sees their week, sees what it owes them, and clears it from
this screen instead of navigating to find it.

FIRST VIEWPORT: name and date; a full-width Mon–Sun spine, each day a cell with
attendance dot, hours and leave, today marked; hours-this-week beside it. Below
left, "Needs you" — rows tagged with the day they belong to, each carrying its
own verb button. Right rail: leave balance, attendance, documents. Quick actions
last.

FORM: The Week Spine, index 4 of 7 on my ordered list, dealt lead by seed
b40ff78d (surface scope, operate).

FINISH: unreviewed and undocumented is unfinished; this build ends with the
finish review, the verdict, DESIGN.md, and every shipping raster carrying its
provenance.

Unresolved: stale items (older than the shown week) need a permanent home in
"Needs you" — the spine alone cannot carry them. This is the direction's named
risk and the build must answer it.
