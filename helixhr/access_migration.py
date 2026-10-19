"""Plan 2026-10-18-001 U5: today's access as org-scoped assignment rows.

Release 2 ships the read-only half, so it can run on production before the
switch:

- `plan_rows(state)` -- the pure mapping, one table (below), shared by the
  dry-run, the Release 3 patch and the U18 parity check.
- `dry_run()` -- `bench --site <site> execute helixhr.access_migration.dry_run`.
  Reads only. Writes two files under the site's private folder, readable by
  the bench user alone: a per-person report (widened / narrowed by design /
  unchanged, with the rows each person would get) and a snapshot of every
  user's resolver outputs today, the baseline U4 and U18 compare against.

The mapping keeps everyone's reach (R26). Where the new model can only keep
it by giving more, the row is still enabled and the widening is listed. The
only narrowing is a named security fix (`SECURITY_FIXES`).

| Today | Rows (all `origin=migrated`) |
|---|---|
| HR Manager / HR User with an Active Employee | that role at the home company |
| HR Manager / HR User / Portal Admin, no Employee | that role, All orgs, empty anchor |
| Report Manager, IT Team, Notification Manager, no Employee | none (no reach today) |
| Portal Admin with an Employee | home + its Company-only User Permission companies |
| Report Manager | Reports at home, export on |
| Reports-only grant | Reports at the grant's companies, the grant's export flag |
| All-data grant | Org viewer at the grant's companies, the grant's export flag |
| Delivery Manager | home + every org where they are a project member |
| IT Team, Notification Manager | home |
| Inactive anchor, disabled user, or a grant that does not apply | the same rows, disabled, with the reason |

System Managers get no rows: they are unscoped and not assignable (R5).
"""

import json
import os

import frappe

HR_MANAGER = "HR Manager"
HR_USER = "HR User"
PORTAL_ADMIN = "HelixHR Portal Admin"
REPORTS = "HelixHR Report Manager"
ORG_VIEWER = "HelixHR Org Viewer"
DELIVERY_MANAGER = "HelixHR Delivery Manager"
IT_ADMIN = "IT Team"
NOTIFICATION_MANAGER = "HelixHR Notification Manager"
SYSTEM_MANAGER = "System Manager"

# Roles whose holders today reach something through their Employee anchor.
_HOME_ROLES = (HR_MANAGER, HR_USER, PORTAL_ADMIN, REPORTS, DELIVERY_MANAGER, IT_ADMIN, NOTIFICATION_MANAGER)
# Of those, the ones that reach every company today when there is no Employee.
_ANCHORLESS_ALL_ORGS = (HR_MANAGER, HR_USER, PORTAL_ADMIN)
_TRACKED_ROLES = (*_HOME_ROLES, SYSTEM_MANAGER)

_GRANT_ROLE = {"All data": ORG_VIEWER, "Reports only": REPORTS}

# The deliberate narrowings, by key. Anything else that narrows is a bug.
SECURITY_FIXES = {
	"left_portal_admin": "A Left or Inactive Portal Admin no longer administers the portal.",
	"portal_admin_scope": (
		"Editing the global report matrix needs All-orgs authority, and email templates are "
		"limited to its own orgs."
	),
	"hr_desk_reach": "From Release 4, Desk HR reach is limited to the assigned orgs.",
	"company_move": "A company move switches assignments off until they are re-confirmed.",
}

_NOT_ACTIVE = "Employee is not Active"
_USER_DISABLED = "User is disabled"
_GRANT_MOVED = "Holder moved since the grant was saved"


def _anchor(employees):
	"""(state, row) from a login's Employee rows, newest first -- the same
	answer `utils.active_employee_for_user` gives, on data already in hand."""
	for row in employees:
		if row["status"] == "Active":
			return "active", row
	return ("inactive", employees[0]) if employees else ("none", None)


def _plural(count, word):
	return f"{count} {word}" if count == 1 else f"{count} {word}s"


def plan_rows(state):
	"""``{user: {employee, home_company, rows, widened, narrowed}}`` for every
	user who gets a row or a note. Pure: reads nothing but ``state``.

	``state`` is ``{"companies": [...], "org_projects": {company: count},
	"users": {user: {enabled, roles, employees, company_permissions, grant,
	project_orgs}}}`` -- the shape `collect_state` reads from the site.
	``widened`` is a list of sentences; ``narrowed`` a list of ``{fix, text}``
	whose ``fix`` is always a `SECURITY_FIXES` key.
	"""
	known = set(state["companies"])
	plan = {}
	for user, info in state["users"].items():
		if SYSTEM_MANAGER in info["roles"]:
			continue
		answer = _plan_user(info, known, state["org_projects"])
		if answer["rows"] or answer["widened"] or answer["narrowed"]:
			plan[user] = answer
	return plan


def _plan_user(info, known, org_projects):
	state, anchor = _anchor(info["employees"])
	home = anchor["company"] if anchor else None
	base_reason = None
	if not info["enabled"]:
		base_reason = _USER_DISABLED
	elif state == "inactive":
		base_reason = _NOT_ACTIVE
	rows = {}
	widened, narrowed = [], []

	def add(role, company=None, export=0, reason=base_reason):
		key = (role, company or "ALL")
		row = rows.setdefault(
			key,
			{
				"role": role,
				"all_orgs": 0 if company else 1,
				"company": company,
				"allow_export": 0,
				"enabled": 0,
				"disabled_reason": reason,
				"origin": "migrated",
			},
		)
		row["allow_export"] = int(bool(row["allow_export"] or export))
		if not reason:
			row["enabled"] = 1
			row["disabled_reason"] = None

	roles = info["roles"]
	for role in _HOME_ROLES:
		if role not in roles:
			continue
		if state == "none":
			if role in _ANCHORLESS_ALL_ORGS:
				add(role)
				if role == HR_USER:
					widened.append(
						"HR User in all orgs: timesheet oversight and matrix reports become visible in the portal."
					)
				elif role == PORTAL_ADMIN:
					# Today this login edits portal settings but its admin scope is
					# empty: no export log, no role grants, no approver cleanup.
					widened.append(
						"Portal Admin in all orgs: export log, role grants and approver cleanup become available."
					)
			elif role == DELIVERY_MANAGER:
				for company in sorted(info["project_orgs"]):
					add(role, company)
			continue
		add(role, home, export=1 if role == REPORTS else 0)

	if PORTAL_ADMIN in roles and state != "none":
		# Inactive: the same rows, disabled by `base_reason`.
		for company in dict.fromkeys(info["company_permissions"]):
			if company in known and company != home:
				add(PORTAL_ADMIN, company)
				widened.append(f"Portal Admin in {company}: full admin (today read and grant only).")
		fix = "left_portal_admin" if state == "inactive" else "portal_admin_scope"
		narrowed.append({"fix": fix, "text": SECURITY_FIXES[fix]})

	if DELIVERY_MANAGER in roles and state != "none":
		for company in sorted(info["project_orgs"]):
			add(DELIVERY_MANAGER, company)
	if DELIVERY_MANAGER in roles and state != "inactive":
		orgs = ({home} if home else set()) | set(info["project_orgs"])
		for company in sorted(orgs):
			today, after = info["project_orgs"].get(company, 0), org_projects.get(company, 0)
			if after > today:
				widened.append(
					f"Delivery Manager in {company}: {_plural(today, 'project')} today, {after} after."
				)

	if state == "active" and roles & {HR_MANAGER, HR_USER}:
		narrowed.append({"fix": "hr_desk_reach", "text": SECURITY_FIXES["hr_desk_reach"]})

	grant = info["grant"]
	if grant and grant["level"] in _GRANT_ROLE:
		reason = base_reason
		if not reason and not grant["enabled"]:
			reason = grant.get("disabled_reason") or "Grant is turned off"
		if not reason and (
			state != "active" or grant["employee"] != anchor["name"] or grant["home_company"] != home
		):
			reason = _NOT_ACTIVE if state != "active" else _GRANT_MOVED
		for company in grant["companies"]:
			if company in known:
				add(_GRANT_ROLE[grant["level"]], company, export=grant["allow_export"], reason=reason)

	if base_reason:
		# Nothing is enabled, so nothing is gained.
		widened = []
	ordered = sorted(rows.values(), key=lambda row: (row["role"], not row["all_orgs"], row["company"] or ""))
	return {
		"employee": anchor["name"] if anchor and state == "active" else None,
		"home_company": home if state == "active" else None,
		"rows": ordered,
		"widened": widened,
		"narrowed": narrowed,
	}


def collect_state():
	"""Today's access, read from the site in a handful of set queries, in the
	shape `plan_rows` takes. Reads only."""
	holders = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ["in", list(_TRACKED_ROLES)]},
		fields=["parent", "role"],
	)
	grants = {
		row.user: row
		for row in frappe.get_all(
			"HelixHR Company Access",
			fields=[
				"name",
				"user",
				"level",
				"allow_export",
				"enabled",
				"employee",
				"home_company",
				"disabled_reason",
			],
		)
	}
	users = ({row.parent for row in holders} | set(grants)) - {"Administrator", "Guest"}
	if not users:
		return {"companies": [], "org_projects": {}, "users": {}}
	user_list = sorted(users)

	enabled = dict(
		frappe.get_all("User", filters={"name": ["in", user_list]}, fields=["name", "enabled"], as_list=True)
	)
	info = {
		user: {
			"enabled": bool(enabled.get(user)),
			"roles": set(),
			"employees": [],
			"company_permissions": [],
			"grant": None,
			"project_orgs": {},
		}
		for user in user_list
	}
	for row in holders:
		if row.parent in info:
			info[row.parent]["roles"].add(row.role)
	for row in frappe.get_all(
		"Employee",
		filters={"user_id": ["in", user_list]},
		fields=["name", "company", "status", "user_id"],
		order_by="creation desc, name desc",
	):
		info[row.user_id]["employees"].append(
			{"name": row.name, "company": row.company, "status": row.status}
		)
	for row in frappe.get_all(
		"User Permission",
		filters={
			"user": ["in", user_list],
			"allow": "Company",
			"apply_to_all_doctypes": 0,
			"applicable_for": "Company",
		},
		fields=["user", "for_value"],
		order_by="creation asc",
	):
		info[row.user]["company_permissions"].append(row.for_value)

	grant_companies = {}
	for row in frappe.get_all(
		"HelixHR Company Access Company",
		filters={"parenttype": "HelixHR Company Access"},
		fields=["parent", "company"],
		order_by="idx asc",
	):
		grant_companies.setdefault(row.parent, []).append(row.company)
	for user, grant in grants.items():
		if user in info:
			info[user]["grant"] = {**grant, "companies": grant_companies.get(grant.name, [])}

	project_company = dict(frappe.get_all("Project", fields=["name", "company"], as_list=True))
	for row in frappe.get_all(
		"Project User",
		filters={"parenttype": "Project", "user": ["in", user_list]},
		fields=["parent", "user"],
	):
		company = project_company.get(row.parent)
		if company:
			orgs = info[row.user]["project_orgs"]
			orgs[company] = orgs.get(company, 0) + 1
	org_projects = {}
	for company in project_company.values():
		if company:
			org_projects[company] = org_projects.get(company, 0) + 1

	return {
		"companies": frappe.get_all("Company", pluck="name", order_by="name asc"),
		"org_projects": org_projects,
		"users": info,
	}


def snapshot(users):
	"""Every resolver's answer for each of ``users`` today -- the baseline the
	Release 3 resolvers are compared against (U4 characterization, U18
	parity). Report access is recorded per catalog report as the companies it
	runs for and whether each exports."""
	from helixhr.reports import CATALOG
	from helixhr.utils import (
		can_admin_portal,
		resolve_admin_scope,
		resolve_portal_admin_scope,
		resolve_project_read_scope,
		resolve_project_scope,
		resolve_read_scope,
		resolve_report_access,
	)

	result = {}
	for user in users:
		reports = {}
		for entry in CATALOG:
			access = resolve_report_access(user, entry["key"])
			if access.get("can_run"):
				reports[entry["key"]] = sorted(
					[row["company"], bool(row["can_export"])] for row in access.get("companies") or []
				)
		result[user] = {
			"admin": _plain(resolve_admin_scope(user)),
			"read": _plain(resolve_read_scope(user)),
			"project": _plain(resolve_project_scope(user)),
			"project_read": _plain(resolve_project_read_scope(user)),
			"portal_admin": _plain(resolve_portal_admin_scope(user)),
			"can_admin_portal": bool(can_admin_portal(user)),
			"reports": reports,
		}
	return result


def _plain(scope):
	"""A scope dict with sorted lists, so two snapshots compare as JSON."""
	return {
		key: sorted(value) if isinstance(value, list | set | tuple) else value for key, value in scope.items()
	}


def dry_run():
	"""What migration would do, person by person, plus today's resolver
	snapshot. Reads only; returns the two file paths.

	The files go to ``private/access_migration/`` with mode 0600: they name
	people and their access, so they are not printed, logged or attached as
	a File record."""
	state = collect_state()
	plan = plan_rows(state)
	people = []
	summary = {"widened": 0, "narrowed_by_design": 0, "unchanged": 0}
	for user in sorted(plan):
		answer = plan[user]
		# Both can apply to one person; the owner reviews each.
		outcomes = [
			name
			for name, notes in (("widened", answer["widened"]), ("narrowed_by_design", answer["narrowed"]))
			if notes
		] or ["unchanged"]
		for outcome in outcomes:
			summary[outcome] += 1
		people.append({"user": user, "outcomes": outcomes, **answer})

	stamp = frappe.utils.now_datetime().strftime("%Y%m%dT%H%M%S")
	report_path = _write_private(
		f"dry_run-{stamp}.json", {"generated": stamp, "summary": summary, "people": people}
	)
	snapshot_path = _write_private(
		f"snapshot-{stamp}.json", {"generated": stamp, "users": snapshot(sorted(state["users"]))}
	)
	return {"report": report_path, "snapshot": snapshot_path, "summary": summary}


def _write_private(name, payload):
	folder = frappe.get_site_path("private", "access_migration")
	os.makedirs(folder, mode=0o700, exist_ok=True)
	os.chmod(folder, 0o700)
	path = os.path.abspath(os.path.join(folder, name))
	descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
	with os.fdopen(descriptor, "w") as handle:
		json.dump(payload, handle, indent=1, default=str)
	os.chmod(path, 0o600)
	return path
