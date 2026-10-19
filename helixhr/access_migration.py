"""Today's access as org-scoped assignment rows (plan 2026-10-18-001 U5,
reshaped by plan 2026-10-19-001 U12 for the assignment model).

The read-only half ships before the switch, so it can run on production:

- `plan_rows(state)` -- the pure mapping, one table (below), shared by the
  dry-run, the Release B patch and the parity check.
- `dry_run()` -- `bench --site <site> execute helixhr.access_migration.dry_run`.
  Reads only. Writes two files under the site's private folder, readable by
  the bench user alone: a per-person report (the rows each person would get,
  and what widens or narrows) and a snapshot of today's scope answers, the
  baseline the parity check compares against.

Assignments carry no employee anchor and no enabled flag: a row that exists
is in force, and the person-level gate (an enabled User, and an Active
Employee when they have any) applies at read time. So the mapping keeps rows
for inactive people and disabled users -- reactivating them restores reach,
as it does today -- and writes no rows for access that reaches nothing today.

Every widening and narrowing is a structured record naming the snapshot keys
it moves, so the parity check can tell a declared change from a bug. The only
narrowings are the named `SECURITY_FIXES`.

| Today | Rows (all `origin=migrated`) |
|---|---|
| HR Manager / HR User with an Employee | that role at the person's company |
| HR Manager / HR User / Portal Admin, no Employee | that role, All orgs |
| Report Manager, IT Team, no Employee | none (no reach today) |
| Portal Admin with an Employee | its company + its Company-only User Permission companies |
| Report Manager | Reports at the person's company, export on |
| Reports-only grant | Reports at the grant's companies, the grant's export flag |
| All-data grant | Org viewer at the grant's companies, the grant's export flag |
| Disabled grant, or a grant saved against another Employee | none (no reach today) |
| Delivery Manager | the person's company + every org where they are a project member |
| IT Team | the person's company |
| Notification Manager | All orgs (message templates are global) |
| System Manager | none; unscoped by role, declared when that widens today's reach |
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

# Roles whose holders today reach something through their Employee's company.
_HOME_ROLES = (HR_MANAGER, HR_USER, PORTAL_ADMIN, REPORTS, DELIVERY_MANAGER, IT_ADMIN)
# Of those, the ones that reach every company today when there is no Employee.
_ANCHORLESS_ALL_ORGS = (HR_MANAGER, HR_USER, PORTAL_ADMIN)
_TRACKED_ROLES = (*_HOME_ROLES, NOTIFICATION_MANAGER, SYSTEM_MANAGER)

_GRANT_ROLE = {"All data": ORG_VIEWER, "Reports only": REPORTS}

# The per-person answers `snapshot` records. A widening or narrowing record
# names the ones it moves; the parity check treats any other move as a bug.
SNAPSHOT_KEYS = (
	"admin",
	"read",
	"project",
	"project_read",
	"portal_admin",
	"can_admin_portal",
	"reports",
	"it_scope",
	"oversight",
	"people_access_reach",
	"flags",
)

# The deliberate narrowings, by key, with the snapshot keys each moves.
# Anything else that narrows is a bug.
SECURITY_FIXES = {
	"left_portal_admin": "A Left or Inactive Portal Admin no longer administers the portal.",
	"global_settings": (
		"The report matrix, email theme and message templates need All-orgs authority; "
		"a Portal Admin on named orgs keeps its orgs' admin."
	),
}
_FIX_KEYS = {
	"left_portal_admin": ["can_admin_portal", "portal_admin", "people_access_reach", "flags"],
	"global_settings": ["flags"],
}


def _anchor(employees):
	"""(state, row) from a login's Employee rows, newest first -- the same
	answer `utils.active_employee_for_user` gives, on data already in hand."""
	for row in employees:
		if row["status"] == "Active":
			return "active", row
	return ("inactive", employees[0]) if employees else ("none", None)


def _plural(count, word):
	return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _widened(key, resolver_keys, text):
	return {"key": key, "resolver_keys": list(resolver_keys), "text": text}


def _narrowed(fix):
	return {"fix": fix, "resolver_keys": list(_FIX_KEYS[fix]), "text": SECURITY_FIXES[fix]}


def plan_rows(state):
	"""``{user: {own_company, rows, widened, narrowed}}`` for every user who
	gets a row or a note. Pure: reads nothing but ``state``.

	``state`` is ``{"companies": [...], "org_projects": {company: count},
	"users": {user: {enabled, roles, employees, company_permissions, grant,
	project_orgs}}}`` -- the shape `collect_state` reads from the site.
	``widened`` is a list of ``{key, resolver_keys, text}``; ``narrowed`` a list
	of ``{fix, resolver_keys, text}`` whose ``fix`` is a `SECURITY_FIXES` key.
	"""
	known = set(state["companies"])
	plan = {}
	for user, info in state["users"].items():
		answer = _plan_user(info, known, state["org_projects"])
		if answer["rows"] or answer["widened"] or answer["narrowed"]:
			plan[user] = answer
	return plan


def _plan_user(info, known, org_projects):
	state, anchor = _anchor(info["employees"])
	home = anchor["company"] if anchor else None
	# Nothing reaches anything while the person is gated off, so nothing is
	# gained either; their rows are still written for reactivation.
	gated = not info["enabled"] or state == "inactive"
	roles = info["roles"]
	rows = {}
	widened, narrowed = [], []

	def add(role, company=None, export=0):
		row = rows.setdefault(
			(role, company or "ALL"),
			{
				"role": role,
				"all_orgs": 0 if company else 1,
				"company": company,
				"allow_export": 0,
				"origin": "migrated",
			},
		)
		row["allow_export"] = int(bool(row["allow_export"] or export))

	if SYSTEM_MANAGER in roles:
		# Unscoped by role, never assignable (R5). Today an anchored System
		# Manager is limited to their company in the portal: that widens.
		if state == "active" and info["enabled"]:
			widened.append(
				_widened(
					"system_manager_unscoped",
					[
						"admin",
						"read",
						"project",
						"project_read",
						"portal_admin",
						"reports",
						"oversight",
						"flags",
					],
					f"System Manager: portal HR reach widens from {home} to all orgs.",
				)
			)
		return _answer(home if state == "active" else None, rows, widened, narrowed)

	for role in _HOME_ROLES:
		if role not in roles:
			continue
		if state == "none":
			if role in _ANCHORLESS_ALL_ORGS:
				add(role)
			elif role == DELIVERY_MANAGER:
				for company in sorted(info["project_orgs"]):
					add(role, company)
			continue
		add(role, home, export=1 if role == REPORTS else 0)

	if NOTIFICATION_MANAGER in roles:
		add(NOTIFICATION_MANAGER)

	if state == "none" and HR_USER in roles:
		widened.append(
			_widened(
				"hr_user_all_orgs",
				["oversight", "reports", "flags"],
				"HR User in all orgs: timesheet oversight and matrix reports become visible in the portal.",
			)
		)
	if state == "none" and PORTAL_ADMIN in roles:
		# Today this login edits portal settings but its admin scope is empty.
		widened.append(
			_widened(
				"portal_admin_all_orgs",
				["portal_admin", "people_access_reach", "flags"],
				"Portal Admin in all orgs: export log, role grants and approver cleanup become available.",
			)
		)

	if PORTAL_ADMIN in roles and state != "none":
		for company in dict.fromkeys(info["company_permissions"]):
			if company in known and company != home:
				add(PORTAL_ADMIN, company)
				widened.append(
					_widened(
						"portal_admin_extra_org",
						["portal_admin"],
						f"Portal Admin in {company}: full admin (today read and grant only).",
					)
				)
		narrowed.append(_narrowed("left_portal_admin" if state == "inactive" else "global_settings"))

	if DELIVERY_MANAGER in roles and state != "none":
		for company in sorted(info["project_orgs"]):
			add(DELIVERY_MANAGER, company)
	if DELIVERY_MANAGER in roles:
		orgs = ({home} if home else set()) | set(info["project_orgs"])
		for company in sorted(orgs):
			today, after = info["project_orgs"].get(company, 0), org_projects.get(company, 0)
			if after > today:
				widened.append(
					_widened(
						"delivery_manager_org_projects",
						["project", "project_read", "reports"],
						f"Delivery Manager in {company}: {_plural(today, 'project')} today, {after} after.",
					)
				)

	grant = info["grant"]
	# A disabled grant, or one saved against an Employee the person no longer
	# is, reaches nothing today. A grant on an inactive person is kept: today
	# it comes back when they are reactivated.
	if (
		grant
		and grant["level"] in _GRANT_ROLE
		and grant["enabled"]
		and state != "none"
		and grant["employee"] == anchor["name"]
		and grant["home_company"] == home
	):
		for company in grant["companies"]:
			if company in known:
				add(_GRANT_ROLE[grant["level"]], company, export=grant["allow_export"])

	if gated:
		widened = []
	return _answer(home, rows, widened, narrowed)


def _answer(own_company, rows, widened, narrowed):
	ordered = sorted(rows.values(), key=lambda row: (row["role"], not row["all_orgs"], row["company"] or ""))
	return {"own_company": own_company, "rows": ordered, "widened": widened, "narrowed": narrowed}


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
	"""Today's scope answers: ``{"users": {user: {key: answer}}, "recipients":
	{company: {role: [user]}}}`` with one entry per `SNAPSHOT_KEYS` key -- the
	baseline the parity check compares the switched resolvers against. Report
	access is recorded per catalog report as the companies it runs for and
	whether each exports; flags are the bootstrap predicates that take a user."""
	from helixhr import api
	from helixhr.reports import CATALOG
	from helixhr.utils import (
		_company_role_holders,
		can_admin_portal,
		grantable_companies,
		resolve_admin_scope,
		resolve_portal_admin_scope,
		resolve_project_read_scope,
		resolve_project_scope,
		resolve_read_scope,
		resolve_report_access,
		session_company,
	)

	answers = {}
	for user in users:
		reports = {}
		for entry in CATALOG:
			access = resolve_report_access(user, entry["key"])
			if access.get("can_run"):
				reports[entry["key"]] = sorted(
					[row["company"], bool(row["can_export"])] for row in access.get("companies") or []
				)
		admin = bool(can_admin_portal(user))
		it_company = session_company(user) if IT_ADMIN in frappe.get_roles(user) else None
		reach = grantable_companies(user) if admin else None
		answers[user] = {
			"admin": _plain(resolve_admin_scope(user)),
			"read": _plain(resolve_read_scope(user)),
			"project": _plain(resolve_project_scope(user)),
			"project_read": _plain(resolve_project_read_scope(user)),
			"portal_admin": _plain(resolve_portal_admin_scope(user)),
			"can_admin_portal": admin,
			"reports": reports,
			"it_scope": [it_company] if it_company else [],
			"oversight": _plain(api._oversight_scope(user) or {}) or None,
			"people_access_reach": sorted(reach) if reach else None,
			"flags": {
				"can_configure": bool(api._is_hr(user)),
				"can_handle_requests": bool(api._is_hr(user) or api._holds_routed_role(user)),
				"can_edit_email_templates": bool(api._can_edit_email_templates(user)),
				"can_run_reports": bool(api._can_run_reports(user)),
				"can_oversee_timesheets": bool(api._can_oversee_timesheets(user)),
				"can_see_all_pending": bool(api._can_see_all_pending(user)),
				"can_manage_documents": bool(api._can_manage_documents(user)),
			},
		}
	recipients = {
		company: {role: sorted(_company_role_holders(role, company)) for role in (HR_MANAGER, IT_ADMIN)}
		for company in frappe.get_all("Company", pluck="name", order_by="name asc")
	}
	return {"users": answers, "recipients": recipients}


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
		f"snapshot-{stamp}.json", {"generated": stamp, **snapshot(sorted(state["users"]))}
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
