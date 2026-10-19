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

The switch (plan 2026-10-19-001 U8):

- `migrate()` -- the `migrate_access_assignments` patch: writes the planned
  rows for everyone who has no row and no access change yet.
- `parity_check(snapshot_path)` -- reads only: the new resolvers' answers
  against the dry-run's snapshot, every difference labelled. Any undeclared
  one blocks reopening the site.

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
| System Manager | none; unscoped by role, declared when that widens today's reach, gated (R4) when disabled or Left |
"""

import json
import os

import frappe

from helixhr.utils import ALL_ORGS_SCOPE_KEY, clear_role_scope_cache

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
	"person_gate": (
		"A disabled login, or a person whose Employee records are all Left or Inactive, reaches "
		"nothing in the portal -- a Left Portal Admin included. Reactivating them restores it."
	),
	"global_settings": (
		"The report matrix, email theme and message templates need All-orgs authority; "
		"a Portal Admin on named orgs keeps its orgs' admin."
	),
	"empty_it_queue": (
		"IT Admin with no Employee record handled no org's requests; its empty request "
		"page is no longer shown."
	),
}
_FIX_KEYS = {
	"person_gate": list(SNAPSHOT_KEYS),
	"global_settings": ["flags"],
	"empty_it_queue": ["flags"],
}

# HR and IT mail recipients the switch moves by design, by rule. The snapshot
# records them per (org, role), not per person, so they are matched here
# rather than through a person's notes; a move no rule explains is undeclared.
DECLARED_RECIPIENT_CHANGES = {
	"system_manager_elsewhere": (
		"A System Manager is never assigned (R5): holding HR Manager or IT Team, they are mailed "
		"for the org of their Active Employee only, never for another org."
	),
	"unscoped_hr_not_it": (
		"An HR Manager or System Manager with no Employee record was mailed every org's IT "
		"requests as an unscoped admin; IT mail now goes to IT Admin assignments only."
	),
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
		# Unscoped by role, never assignable (R5), but behind the person
		# gate (R4), which today's code never applied to them: a disabled or
		# all-Left System Manager loses their portal reach.
		if gated:
			return _answer(home, rows, [], [_narrowed("person_gate")])
		# Today an anchored System Manager is limited to their company in
		# the portal: that widens.
		if state == "active":
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
						"it_scope",
						"oversight",
						"flags",
					],
					f"System Manager: portal HR reach widens from {home} to all orgs.",
				)
			)
		elif IT_ADMIN in roles:
			# Today the IT queue is the Active Employee's company: none here.
			widened.append(
				_widened(
					"system_manager_it_all_orgs",
					["it_scope"],
					"System Manager with IT Team and no Employee: the IT queue widens from none to all orgs.",
				)
			)
		return _answer(home, rows, widened, narrowed)

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

	if state == "none" and IT_ADMIN in roles and info["enabled"]:
		narrowed.append(_narrowed("empty_it_queue"))

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
						["portal_admin", "people_access_reach"],
						f"Portal Admin in {company}: full admin (today read and grant only).",
					)
				)
		if state == "active":
			narrowed.append(_narrowed("global_settings"))

	if DELIVERY_MANAGER in roles and state != "none":
		for company in sorted(info["project_orgs"]):
			add(DELIVERY_MANAGER, company)
	if DELIVERY_MANAGER in roles:
		# Today's answer is membership ("assigned"), after it is the orgs, so
		# the scope's shape moves even where the count does not: declared
		# per org either way.
		orgs = ({home} if home else set()) | set(info["project_orgs"])
		for company in sorted(orgs):
			today, after = info["project_orgs"].get(company, 0), org_projects.get(company, 0)
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
		granted = [company for company in grant["companies"] if company in known]
		for company in granted:
			add(_GRANT_ROLE[grant["level"]], company, export=grant["allow_export"])
		if granted and grant["level"] == "Reports only":
			# Today's grant tier runs company-scoped catalog entries only; a
			# Reports row runs every entry, as Report Manager always has.
			widened.append(
				_widened(
					"reports_grant_all_catalog",
					["reports"],
					f"Reports in {', '.join(granted)}: every catalog report (today company reports only).",
				)
			)

	if gated and rows:
		# Today the resolvers mostly ignore a disabled login and an all-Left
		# person; after the switch the person gate (R4) zeroes them. Declared,
		# so the parity check can tell it from a bug.
		widened, narrowed = [], [_narrowed("person_gate")]
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
	"""The resolvers' answers: ``{"users": {user: {key: answer}}, "recipients":
	{company: {role: [user]}}}`` with one entry per `SNAPSHOT_KEYS` key. Report
	access is recorded per catalog report as the companies it runs for and
	whether each exports; flags are the bootstrap predicates that take a user.

	Whatever code is deployed answers. The baseline the parity check compares
	against is the dry-run's snapshot taken with Release A still deployed
	(deploy checklist step 3); the parity check calls this again after the
	switch for the new answers, in the same shape."""
	from helixhr import api
	from helixhr.reports import CATALOG
	from helixhr.utils import (
		_company_role_holders,
		can_admin_portal,
		read_companies,
		resolve_admin_scope,
		resolve_portal_admin_scope,
		resolve_project_read_scope,
		resolve_project_scope,
		resolve_read_scope,
		resolve_report_access,
		role_scope,
	)

	companies = frappe.get_all("Company", pluck="name", order_by="name asc")
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
		# The IT queue: the orgs of the IT Team rows, behind the Frappe role
		# (Release A recorded the Active Employee's company here).
		it_scope = []
		if IT_ADMIN in frappe.get_roles(user):
			it_scope = read_companies(role_scope(user, IT_ADMIN))
			it_scope = companies if it_scope is None else it_scope
		reach = api.people_access_reach(user)
		answers[user] = {
			"admin": _plain(resolve_admin_scope(user)),
			"read": _plain(resolve_read_scope(user)),
			"project": _plain(resolve_project_scope(user)),
			"project_read": _plain(resolve_project_read_scope(user)),
			"portal_admin": _plain(resolve_portal_admin_scope(user)),
			"can_admin_portal": admin,
			"reports": reports,
			"it_scope": sorted(it_scope),
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
		for company in companies
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


# --- The switch (plan 2026-10-19-001 U8) --------------------------------------
#
# `migrate` writes the planned rows once, from the patch; `parity_check` then
# proves the switched resolvers against the dry-run's snapshot (R25, R26).

_MIGRATION_SKIP = "has assignment rows or an access change"


def apply_rows(plan):
	"""Write ``plan``'s rows as `HelixHR Access Assignment` (origin
	``migrated``) for every user who has neither a row nor a `HelixHR Access
	Change` yet, so a re-run, or a person whose rows were all removed through
	the save, is left alone. Returns ``{"migrated": [user], "skipped":
	[user]}``.

	Side effects off: legacy grants and User Permissions are never touched,
	no export expires and no mail goes out. The only Frappe role written is
	`HelixHR Org Viewer`, which the old code never reads -- every other role
	is already held, or (a "Reports only" grant's Reports rows) deliberately
	not given, so a rollback to the grant code changes nothing."""
	users = sorted(user for user, answer in plan.items() if answer["rows"])
	if not users:
		return {"migrated": [], "skipped": []}
	done = set(
		frappe.get_all(
			"HelixHR Access Assignment", filters={"user": ["in", users]}, pluck="user", distinct=True
		)
	) | set(
		frappe.get_all(
			"HelixHR Access Change",
			filters={"target_user": ["in", users]},
			pluck="target_user",
			distinct=True,
		)
	)
	viewers = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "parent": ["in", users], "role": ORG_VIEWER},
			pluck="parent",
		)
	)
	migrated = []
	for user in users:
		if user in done:
			continue
		for row in plan[user]["rows"]:
			frappe.get_doc(
				{
					"doctype": "HelixHR Access Assignment",
					"user": user,
					"role": row["role"],
					"all_orgs": row["all_orgs"],
					# "" not None: a None Company picks up the session's default
					# company on insert, which an All-orgs row refuses.
					"company": row["company"] or "",
					"allow_export": row["allow_export"],
					"origin": "migrated",
				}
			).insert(ignore_permissions=True)
		if user not in viewers and any(row["role"] == ORG_VIEWER for row in plan[user]["rows"]):
			_add_has_role(user, ORG_VIEWER)
		migrated.append(user)

	clear_role_scope_cache()
	for user in migrated:
		frappe.clear_cache(user=user)
	return {"migrated": migrated, "skipped": sorted(done)}


def _add_has_role(user, role):
	"""Append ``role`` to ``user`` as a bare `Has Role` row. Not `User.save`:
	that runs the User's hooks (Role Profile re-derivation, contact sync)
	for a role the old code never reads."""
	idx = frappe.get_all(
		"Has Role", filters={"parenttype": "User", "parent": user}, pluck="idx", order_by="idx desc", limit=1
	)
	frappe.get_doc(
		{
			"doctype": "Has Role",
			"parenttype": "User",
			"parentfield": "roles",
			"parent": user,
			"role": role,
			"idx": (idx[0] if idx else 0) + 1,
		}
	).db_insert()


def migrate():
	"""Today's access as assignment rows: `apply_rows` on `plan_rows` of the
	site. Run by the `migrate_access_assignments` patch; safe to re-run."""
	return apply_rows(plan_rows(collect_state()))


# Parity labels. Counted in every report, even at zero.
_LABELS = ("declared widening", "security fix", "declared recipient change", "undeclared")


def parity_check(snapshot_path):
	"""Compare the switched resolvers with the dry-run's snapshot (R26, KTD9).
	Reads only. ``bench --site <site> execute
	helixhr.access_migration.parity_check --args '["<snapshot path>"]'``.

	The legacy state -- Has Role, grants, Company User Permissions, project
	membership -- is what the patch never changes, so `collect_state` and
	`plan_rows` re-derive the plan the migration wrote. Then, per snapshot
	user and `SNAPSHOT_KEYS` key, a new answer that differs from the stored
	one is:

	- "declared widening" when the new answer is wider (`_direction`) and
	  one of the person's ``widened`` records names the key;
	- "security fix" when it is narrower and one of their ``narrowed``
	  records does;
	- "undeclared" otherwise -- a narrowing under a widening note, a widening
	  under a fix, or a move that is neither (a mix, or a reshape).

	Flags are compared one by one (``flags.<name>``), so a flag lost under a
	note that widens another is still caught.

	HR and IT recipients per (org, role) are compared the same way; a move is
	"declared recipient change" when a `DECLARED_RECIPIENT_CHANGES` rule
	explains it. Every migrated person's rows must equal their planned rows
	(a person changed through the save since is listed, not compared). A
	declared key only says the move was expected, so the rows check is what
	ties the new answer to the plan: right rows, and resolvers that read them.

	Writes a 0600 report under the site's private folder and returns
	``{"undeclared": n, "counts": {label: n}, "report": path}``. Any
	undeclared difference blocks reopening the site (deploy checklist)."""
	with open(snapshot_path) as handle:
		baseline = json.load(handle)
	state = collect_state()
	plan = plan_rows(state)
	users = sorted(baseline["users"])
	# Through JSON, so tuples and lists compare as the stored file does.
	current = json.loads(json.dumps(snapshot(users), default=str))

	people = {}
	for user in users:
		answer = plan.get(user) or {"widened": [], "narrowed": []}
		assigned = (state["users"].get(user) or {}).get("project_orgs") or {}
		old, new = _as_callers_see_it(baseline["users"][user]), _as_callers_see_it(current["users"][user])
		for key in SNAPSHOT_KEYS:
			for name, before, after in _moves(key, old.get(key), new.get(key)):
				direction = _direction(key, before, after, assigned)
				widened = sorted(
					{note["key"] for note in answer["widened"] if key in note["resolver_keys"]}
					if direction == "wider"
					else ()
				)
				fixes = sorted(
					{note["fix"] for note in answer["narrowed"] if key in note["resolver_keys"]}
					if direction == "narrower"
					else ()
				)
				label = "declared widening" if widened else "security fix" if fixes else "undeclared"
				people.setdefault(user, []).append(
					{
						"key": name,
						"label": label,
						"direction": direction,
						"notes": widened or fixes,
						"before": before,
						"after": after,
					}
				)

	recipients = _recipient_differences(baseline.get("recipients") or {}, current["recipients"], state)
	rows, changed_through_save = _row_differences(users, plan)
	for difference in rows:
		people.setdefault(difference["user"], []).append(difference)

	counts = dict.fromkeys(_LABELS, 0)
	for difference in [*recipients, *(item for items in people.values() for item in items)]:
		counts[difference["label"]] += 1
	# Microseconds: two runs in one second must not share a report.
	stamp = frappe.utils.now_datetime().strftime("%Y%m%dT%H%M%S.%f")
	path = _write_private(
		f"parity-{stamp}.json",
		{
			"generated": stamp,
			"snapshot": snapshot_path,
			"counts": counts,
			"people": people,
			"recipients": recipients,
			"changed_through_save": changed_through_save,
		},
	)
	return {"undeclared": counts["undeclared"], "counts": counts, "report": path}


def _moves(key, before, after):
	"""``[(name, before, after)]``: the one answer that moved, or for
	``flags`` each flag that did, as ``flags.<flag>``."""
	if before == after:
		return []
	if key == "flags" and isinstance(before, dict) and isinstance(after, dict):
		return [
			(f"flags.{flag}", before.get(flag), after.get(flag))
			for flag in sorted(set(before) | set(after))
			if before.get(flag) != after.get(flag)
		]
	return [(key, before, after)]


def _direction(key, before, after, assigned):
	""""wider", "narrower" or None (both, neither, or a reshape) for one
	moved answer, by the shape of ``key``'s answers. ``assigned`` is the
	person's project orgs, what today's "assigned" project scope reaches."""
	if key in ("admin", "read", "project", "project_read", "portal_admin"):
		return _scope_direction(before or {}, after or {}, assigned)
	if key == "reports":
		return _combine(
			_set_direction(_report_cells(before), _report_cells(after)),
			_set_direction(_report_cells(before, export=True), _report_cells(after, export=True)),
		)
	if key in ("it_scope", "people_access_reach"):
		return _set_direction(set(before or []), set(after or []))
	if key == "oversight":
		if not before or not after:
			return "wider" if after else "narrower"
		return _combine(
			_set_direction(_companies(before["companies"]), _companies(after["companies"])),
			_set_direction(_hr_manager(before), _hr_manager(after)),
		)
	# Booleans: can_admin_portal and each flag.
	if isinstance(before, bool) and isinstance(after, bool):
		return "wider" if after else "narrower"
	return None


def _hr_manager(oversight):
	return {"hr_manager"} if oversight["hr_manager"] else set()


def _companies(names):
	"""A company list as a set, or None for every company."""
	return None if names is None else set(names)


def _scope_cover(scope, assigned):
	"""The companies a snapshot scope reaches, or None for every one."""
	kind = scope.get("kind")
	if kind == "unscoped":
		return None
	if kind == "company":
		return {scope["company"]} if scope.get("company") else set()
	if kind in ("companies", "combined"):
		return set(scope.get("companies") or [])
	if kind == "assigned":
		return set(assigned)
	return set()


def _scope_direction(before, after, assigned):
	old, new = _scope_cover(before, assigned), _scope_cover(after, assigned)
	# "assigned" (project membership) reaches only some of its orgs'
	# projects, so an org scope over at least those orgs is wider.
	if before.get("kind") == "assigned" and after.get("kind") != "assigned":
		return "wider" if new is None or new >= old else None
	if after.get("kind") == "assigned" and before.get("kind") != "assigned":
		return "narrower" if old is None or old >= new else None
	return _set_direction(old, new)


def _report_cells(reports, export=False):
	"""``{(report, company)}`` a snapshot's reports answer runs (or exports)."""
	return {
		(report, company)
		for report, rows in (reports or {}).items()
		for company, can_export in rows
		if can_export or not export
	}


def _set_direction(old, new):
	"""Two coverages, each a set or None for everything."""
	if old == new:
		return None
	if new is None:
		return "wider"
	if old is None:
		return "narrower"
	if new > old:
		return "wider"
	if new < old:
		return "narrower"
	return None


def _combine(*directions):
	"""One direction for several parts of an answer, or None when they
	disagree or none moved."""
	moved = {direction for direction in directions if direction}
	return moved.pop() if len(moved) == 1 else None


def _as_callers_see_it(answers):
	"""``answers`` with ``portal_admin`` behind ``can_admin_portal``, the gate
	every caller of `resolve_portal_admin_scope` passes first. Release A
	answered it for an HR Manager too (their HR scope), though no caller
	ever read that, so it compares as "none" whenever the gate is shut."""
	if answers.get("can_admin_portal"):
		return answers
	return {**answers, "portal_admin": {"kind": "none", "company": None}}


def _recipient_differences(before, after, state):
	"""Per org in the snapshot and role, each recipient added or removed,
	labelled by `DECLARED_RECIPIENT_CHANGES`. An org created since the
	snapshot has no baseline and is not compared."""
	differences = []
	for company in sorted(before):
		for role in sorted(before[company]):
			old = set(before[company][role])
			new = set((after.get(company) or {}).get(role) or [])
			for change, users in (("added", new - old), ("removed", old - new)):
				for user in sorted(users):
					rule = _recipient_rule(change, user, company, state["users"].get(user), role)
					differences.append(
						{
							"company": company,
							"role": role,
							"user": user,
							"change": change,
							"label": "declared recipient change" if rule else "undeclared",
							"notes": [rule] if rule else [],
						}
					)
	return differences


def _recipient_rule(change, user, company, info, role=None):
	"""The `DECLARED_RECIPIENT_CHANGES` key that explains ``user`` being
	``change``d ("added" or "removed") as a recipient in ``company``, or
	None. ``info`` is the user's `collect_state` entry."""
	if not info:
		return None
	state, anchor = _anchor(info["employees"])
	if (
		change == "removed"
		and SYSTEM_MANAGER in info["roles"]
		and state == "active"
		and anchor["company"] != company
	):
		return "system_manager_elsewhere"
	if (
		change == "removed"
		and role == IT_ADMIN
		and state == "none"
		and info["roles"] & {HR_MANAGER, SYSTEM_MANAGER}
	):
		return "unscoped_hr_not_it"
	return None


def _row_differences(users, plan):
	"""Every migrated person's rows against ``plan``: the snapshot users the
	plan gives rows, and anyone holding a migrated row. A person with a
	`HelixHR Access Change` was changed through the save since, so is listed
	in the second return value instead of compared."""
	holders = set(
		frappe.get_all(
			"HelixHR Access Assignment", filters={"origin": "migrated"}, pluck="user", distinct=True
		)
	)
	check = sorted({user for user in users if plan.get(user, {}).get("rows")} | holders)
	if not check:
		return [], []
	changed = set(
		frappe.get_all(
			"HelixHR Access Change",
			filters={"target_user": ["in", check]},
			pluck="target_user",
			distinct=True,
		)
	)
	actual = {}
	for row in frappe.get_all(
		"HelixHR Access Assignment",
		filters={"user": ["in", check]},
		fields=["user", "role", "all_orgs", "company", "allow_export", "origin"],
	):
		actual.setdefault(row.user, set()).add(_row_key(row))
	differences = []
	for user in check:
		if user in changed:
			continue
		planned = {_row_key(row) for row in (plan.get(user) or {}).get("rows", [])}
		found = actual.get(user, set())
		if planned != found:
			differences.append(
				{
					"user": user,
					"key": "rows",
					"label": "undeclared",
					"notes": [],
					"before": sorted(planned),
					"after": sorted(found),
				}
			)
	return differences, sorted(changed)


def _row_key(row):
	return (
		row["role"],
		ALL_ORGS_SCOPE_KEY if row["all_orgs"] else row["company"],
		int(bool(row["allow_export"])),
		row["origin"],
	)
