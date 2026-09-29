import ast
from pathlib import Path

from frappe.tests import IntegrationTestCase

# R8's backstop (P7-U6): no module under `helixhr/` reads, writes, or
# selects a billing rate, costing rate, billing amount, or costing amount --
# the fields the app is written to never touch (KTD1). This is a backstop
# for one failure mode, not the mechanism (KTD9's explicit field allow-lists
# are the mechanism); it exists because R8 is the kind of boundary that
# erodes quietly through one added field in one response, and a test is
# cheaper than a review.
#
# A plain text grep would also flag the prose that documents this very
# constraint -- KTD3/KTD9 are discussed by name, with these field names
# inside backticks, in `preflight.py`, `patches/v1_0/apply_permission_deltas.py`,
# and other tests. So this walks the AST instead of the raw text: it flags
# `doc.billing_rate`-style attribute access and a forbidden name used as a
# standalone string (a dict key, a `fields=[...]` entry, a `get_value`
# fieldname) -- never a name that merely appears inside a longer comment or
# docstring sentence.
_FORBIDDEN = {
	"billing_rate",
	"billing_amount",
	"costing_rate",
	"costing_amount",
	# Plan 2026-09-29-001 U3: the Profile projection reads Employee on its
	# owner's behalf and must never select pay -- `Employee.ctc`, or
	# External Work History's prior-employer `salary` column.
	"ctc",
	"salary",
}

_APP_ROOT = Path(__file__).resolve().parent.parent
# `helixhr/tests/` is out of scope for this scan, on purpose: the plan's own
# test scenario for R8 ("a request that supplies a billing rate or amount is
# rejected or ignored") has to name these exact fields to prove they are
# absent from what gets persisted -- `test_api_timesheet.py`'s
# `test_a_billing_rate_or_amount_on_the_row_is_ignored_...` does exactly
# that. This scan is a backstop against the field reaching *application*
# code (`api.py`, `events.py`, `hooks.py`, doc-event and patch modules) --
# the runtime surface KTD9's allow-lists are meant to hold the line on --
# not a ban on a test asserting the field's absence.
_TESTS_DIR = Path(__file__).resolve().parent


def _iter_python_files():
	for path in _APP_ROOT.rglob("*.py"):
		if _TESTS_DIR in path.parents:
			continue
		# Frontend build output and node_modules never land under helixhr/,
		# but a stray __pycache__ can.
		if "__pycache__" in path.parts:
			continue
		yield path


def _violations_in(path):
	source = path.read_text(encoding="utf-8")
	try:
		tree = ast.parse(source, filename=str(path))
	except SyntaxError:
		# Not a file this app maintains as valid Python (shouldn't happen
		# under helixhr/); skip rather than fail the whole suite on it.
		return []

	found = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN:
			found.append(f"{path}:{node.lineno}: attribute access `.{node.attr}`")
		elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _FORBIDDEN:
			found.append(f"{path}:{node.lineno}: string literal {node.value!r}")
	return found


class TestNoMoneyFields(IntegrationTestCase):
	def test_no_module_references_a_billing_or_costing_rate_or_amount_field(self):
		violations = []
		for path in _iter_python_files():
			violations.extend(_violations_in(path))

		self.assertEqual(
			violations,
			[],
			"helixhr must never read, write, or select a billing/costing rate "
			"or amount field (R8):\n" + "\n".join(violations),
		)
