"""Plan 2026-10-04-001 U2 / KTD7: the one shaper every output renders."""

from frappe.tests import UnitTestCase

from helixhr.reports import cap_rows, shape

COLUMNS = [
	{"fieldname": "employee", "fieldtype": "Link"},
	{"fieldname": "project", "fieldtype": "Link"},
	{"fieldname": "hours", "fieldtype": "Float"},
]


def _data(rows):
	return [row for row in rows if row["_kind"] == "row"]


class TestShape(UnitTestCase):
	def test_two_level_grouping_emits_subtotals_per_level_and_a_grand_total(self):
		rows = [
			{"employee": "E1", "project": "P1", "hours": 1},
			{"employee": "E2", "project": "P1", "hours": 4},
			{"employee": "E1", "project": "P2", "hours": 2},
			{"employee": "E1", "project": "P1", "hours": 3},
		]
		out = shape(COLUMNS, rows, ["employee", "project"])["rows"]
		kinds = [(row["_kind"], row.get("_level"), row.get("employee"), row.get("project")) for row in out]
		self.assertEqual(
			kinds,
			[
				("row", None, "E1", "P1"),
				("row", None, "E1", "P1"),
				("subtotal", 1, "E1", "P1"),
				("row", None, "E1", "P2"),
				("subtotal", 1, "E1", "P2"),
				("subtotal", 0, "E1", None),
				("row", None, "E2", "P1"),
				("subtotal", 1, "E2", "P1"),
				("subtotal", 0, "E2", None),
				("total", None, None, None),
			],
		)
		self.assertEqual(out[2]["hours"], 4)
		self.assertEqual(out[5]["hours"], 6)
		self.assertEqual(out[-1]["hours"], 10)
		self.assertEqual(out[-1]["hours"], sum(row["hours"] for row in _data(out)))

	def test_a_subtotal_labels_itself_in_the_first_non_group_text_column(self):
		"""Plan 2026-10-05-001 U8 (R14)."""
		rows = [
			{"employee": "E1", "project": "P1", "hours": 1},
			{"employee": "E1", "project": "P2", "hours": 2},
		]
		out = shape(COLUMNS, rows, ["employee"])["rows"]
		subtotal = next(row for row in out if row["_kind"] == "subtotal")
		self.assertEqual(subtotal["project"], "Subtotal")
		self.assertEqual(subtotal["employee"], "E1")
		self.assertNotIn("project", out[-1])

	def test_rows_round_once_and_totals_agree(self):
		rows = [{"employee": "E", "project": "P", "hours": h} for h in (0.333, 0.333, 0.334)]
		result = shape(COLUMNS, rows)
		self.assertEqual([row["hours"] for row in _data(result["rows"])], [0.33, 0.33, 0.33])
		self.assertEqual(result["totals"]["hours"], 0.99)

	def test_sort_then_group_keeps_the_sort_inside_each_group(self):
		rows = [
			{"employee": "E2", "project": "P", "hours": 1},
			{"employee": "E1", "project": "P", "hours": 2},
			{"employee": "E1", "project": "P", "hours": 5},
		]
		out = shape(COLUMNS, rows, ["employee"], {"field": "hours", "order": "desc"})["rows"]
		self.assertEqual([(r["employee"], r["hours"]) for r in _data(out)], [("E1", 5), ("E1", 2), ("E2", 1)])

	def test_explicit_totals_list_limits_what_is_summed(self):
		result = shape(COLUMNS, [{"employee": "E", "project": "P", "hours": 2}], totals=())
		self.assertEqual(result["totals"], {})


class TestCapRows(UnitTestCase):
	def test_under_the_cap_is_untouched(self):
		out = shape(COLUMNS, [{"employee": "E", "project": "P", "hours": 1}])["rows"]
		self.assertEqual(cap_rows(out, 5), (out, False))

	def test_ungrouped_over_the_cap_keeps_cap_rows_and_the_full_total(self):
		rows = [{"employee": f"E{i}", "project": "P", "hours": 1} for i in range(10)]
		result = shape(COLUMNS, rows)
		capped, truncated = cap_rows(result["rows"], 4)
		self.assertTrue(truncated)
		self.assertEqual(len(_data(capped)), 4)
		self.assertEqual(capped[-1]["_kind"], "total")
		self.assertEqual(capped[-1]["hours"], 10)
		self.assertEqual(result["total_rows"], 10)

	def test_grouped_cut_lands_on_a_group_boundary(self):
		rows = [{"employee": "A", "project": "P", "hours": 1}] * 3 + [
			{"employee": "B", "project": "P", "hours": 1}
		] * 3
		capped, truncated = cap_rows(shape(COLUMNS, rows, ["employee"])["rows"], 4)
		self.assertTrue(truncated)
		self.assertEqual([r["employee"] for r in _data(capped)], ["A", "A", "A"])
		self.assertEqual(capped[-2]["_kind"], "subtotal")
		self.assertEqual(capped[-1]["hours"], 6)
