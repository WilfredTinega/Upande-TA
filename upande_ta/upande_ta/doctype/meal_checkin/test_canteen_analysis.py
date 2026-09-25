# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Which bucket the Canteen Analysis puts an employee in for a day. Lives here
because frappe only honours IGNORE_TEST_RECORD_DEPENDENCIES in a doctype
folder; the code under test is upande_ta.upande_ta.api.canteen_analysis."""

import unittest

IGNORE_TEST_RECORD_DEPENDENCIES = ["Employee", "Farm", "User"]


class TestCanteenClassify(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api import canteen_analysis as ca

		self.ca = ca

	def test_nobody_excluded_eats(self):
		self.assertIsNone(self.ca.classify(None, None))

	def test_every_sick_type_is_sick_off(self):
		for lt in ("Sick Leave", "Sick Leave (Full Pay)", "Sick Leave (Half Pay)"):
			self.assertEqual(self.ca.classify(None, lt), self.ca.SICK, lt)

	def test_other_leave_is_leave(self):
		for lt in ("Annual Leave", "Maternity Leave", "Compensatory Off", "Leave Without Pay"):
			self.assertEqual(self.ca.classify(None, lt), self.ca.LEAVE, lt)

	def test_off_day_wins_over_leave(self):
		self.assertEqual(self.ca.classify("Weekly Off", "Sick Leave (Full Pay)"), self.ca.OFF_DAY)
		self.assertEqual(self.ca.classify("Mashujaa Day", "Annual Leave"), self.ca.OFF_DAY)


class TestDailyMealCostPricing(unittest.TestCase):
	def setUp(self):
		import datetime

		from upande_ta.upande_ta.api import canteen_analysis as ca

		self.ca = ca
		d = datetime.date
		self.costs = {
			(d(2026, 9, 9), "", "Lunch"): {"cost": 85, "menu": "Rice & Beans"},
			(d(2026, 9, 9), "Karen Roses", "Lunch"): {"cost": 90, "menu": "Pilau"},
			(d(2026, 9, 9), "", "Breakfast"): {"cost": 40, "menu": "Tea & Bread"},
			(d(2026, 9, 10), "", "Lunch"): {"cost": 70, "menu": "Ugali & Sukuma"},
		}

	def test_priced_from_that_exact_date(self):
		self.assertEqual(self.ca.rate_for(self.costs, "2026-09-10", "Kaitet Ltd.", "Lunch")["cost"], 70)
		self.assertEqual(
			self.ca.rate_for(self.costs, "2026-09-09", "Kaitet Ltd.", "Lunch")["menu"], "Rice & Beans"
		)

	def test_company_record_beats_blank(self):
		self.assertEqual(self.ca.rate_for(self.costs, "2026-09-09", "Karen Roses", "Lunch")["cost"], 90)
		# the company's own record only covers the day it was entered for
		self.assertEqual(self.ca.rate_for(self.costs, "2026-09-10", "Karen Roses", "Lunch")["cost"], 70)

	def test_missing_day_or_meal_is_unpriced_never_carried_over(self):
		self.assertIsNone(self.ca.rate_for(self.costs, "2026-09-11", "Kaitet Ltd.", "Lunch"))
		self.assertIsNone(self.ca.rate_for(self.costs, "2026-09-10", "Kaitet Ltd.", "Breakfast"))
		self.assertIsNone(self.ca.rate_for(self.costs, "2026-09-09", "Kaitet Ltd.", "Supper"))

	def test_forecast_and_actual_cost_of_a_day(self):
		out = self.ca.cost_of_day(
			self.costs,
			"2026-09-09",
			"Lunch",
			{"Karen Roses": 10, "Kaitet Ltd.": 5},
			{"Karen Roses": 8, "Kaitet Ltd.": 4},
		)
		self.assertEqual(out["forecast_cost"], 10 * 90 + 5 * 85)
		self.assertEqual(out["actual_cost"], 8 * 90 + 4 * 85)
		self.assertEqual((out["forecast_unpriced"], out["actual_unpriced"]), (0, 0))

	def test_day_not_over_has_no_actual_and_unpriced_day_is_counted(self):
		future = self.ca.cost_of_day(self.costs, "2026-09-10", "Lunch", {"Kaitet Ltd.": 3}, None)
		self.assertEqual((future["forecast_cost"], future["actual_cost"]), (210, None))
		missing = self.ca.cost_of_day(
			self.costs, "2026-09-11", "Lunch", {"Kaitet Ltd.": 3}, {"Kaitet Ltd.": 2}
		)
		self.assertEqual((missing["forecast_cost"], missing["forecast_unpriced"]), (0, 3))
		self.assertEqual((missing["actual_cost"], missing["actual_unpriced"]), (0, 2))


class TestDailyMealCostRecord(unittest.TestCase):
	def tearDown(self):
		import frappe

		frappe.db.rollback()

	def test_one_row_per_meal_type(self):
		from upande_ta.upande_ta.doctype.daily_meal_cost.daily_meal_cost import duplicate_meal_types

		rows = [{"meal_type": "Lunch"}, {"meal_type": "Supper"}, {"meal_type": "Lunch"}]
		self.assertEqual(duplicate_meal_types(rows), ["Lunch"])
		self.assertEqual(duplicate_meal_types(rows[:2]), [])

	def test_title_names_the_lunch(self):
		from upande_ta.upande_ta.doctype.daily_meal_cost.daily_meal_cost import title_for

		title = title_for(
			"2026-09-26",
			[{"meal_type": "Breakfast", "menu": "Tea"}, {"meal_type": "Lunch", "menu": "Rice & Beans"}],
		)
		self.assertTrue(title.endswith(" · Lunch: Rice & Beans"), title)

	def _doc(self, date, rows):
		import frappe

		return frappe.get_doc(
			{
				"doctype": "Daily Meal Cost",
				"date": date,
				"meals": [{"meal_type": m, "menu": menu, "cost_per_meal": c} for m, menu, c in rows],
			}
		)

	def test_duplicate_meal_type_is_refused(self):
		import frappe

		doc = self._doc("2031-01-01", [("Lunch", "Pilau", 100), ("Lunch", "Githeri", 65)])
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_one_record_per_date_and_company(self):
		import frappe

		self._doc("2031-01-02", [("Lunch", "Pilau", 100)]).insert()
		self.assertRaises(
			frappe.DuplicateEntryError, self._doc("2031-01-02", [("Lunch", "Githeri", 65)]).insert
		)
		# another date is fine
		self._doc("2031-01-03", [("Lunch", "Githeri", 65)]).insert()


class TestPunchSequence(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api import canteen_analysis as ca

		self.ca = ca

	def test_first_four_and_the_gaps_between_them(self):
		seq = self.ca.punch_sequence(
			[
				"2026-09-09 12:42:00",
				"2026-09-09 12:00:00",
				"2026-09-09 13:00:30",
				"2026-09-09 12:43:00",
				"2026-09-09 14:00:00",
				"2026-09-09 15:00:00",
			]
		)
		self.assertEqual(seq["count"], 6)
		self.assertEqual(
			seq["times"],
			["2026-09-09 12:00:00", "2026-09-09 12:42:00", "2026-09-09 12:43:00", "2026-09-09 13:00:30"],
		)
		self.assertEqual(seq["intervals"], [42 * 60, 60, 17 * 60 + 30])
		self.assertEqual(seq["more"], 2)

	def test_single_punch_has_no_interval(self):
		seq = self.ca.punch_sequence(["2026-09-09 12:10:00"])
		self.assertEqual((seq["count"], seq["intervals"], seq["more"]), (1, [], 0))

	def test_rows_group_per_employee_ordered_by_first_punch(self):
		punches = [
			{
				"employee": "B",
				"employee_name": "b",
				"company": "C",
				"farm": "F",
				"meal": "Lunch",
				"time": "2026-09-09 12:30:00",
			},
			{
				"employee": "A",
				"employee_name": "a",
				"company": "C",
				"farm": "F",
				"meal": "Lunch",
				"time": "2026-09-09 12:50:00",
			},
			{
				"employee": "B",
				"employee_name": "b",
				"company": "C",
				"farm": "F",
				"meal": "Supper",
				"time": "2026-09-09 18:00:00",
			},
			{
				"employee": "A",
				"employee_name": "a",
				"company": "C",
				"farm": "F",
				"meal": "Breakfast",
				"time": "2026-09-09 07:00:00",
			},
		]
		rows = self.ca.punches_by_employee(punches)
		self.assertEqual([r["employee"] for r in rows], ["A", "B"])
		self.assertEqual(rows[0]["intervals"], [5 * 3600 + 50 * 60])
		self.assertEqual(rows[1]["meals"], ["Lunch", "Supper"])


class TestMissedLunch(unittest.TestCase):
	def setUp(self):
		import datetime

		from upande_ta.upande_ta.api import canteen_analysis as ca

		self.ca = ca
		self.d = lambda day: datetime.date(2026, 9, day)

	def test_expected_without_a_punch_is_missed_once_over(self):
		d = self.d
		rec = self.ca.employee_days(
			d(1),
			d(7),
			d(5),
			off={d(6): "Weekly Off"},
			leave={d(3): {"leave_type": "Sick Leave (Full Pay)"}, d(4): {"leave_type": "Annual Leave"}},
			ate={d(1), d(6)},
		)
		by = {x["date"]: x for x in rec["days"]}
		self.assertEqual(by["2026-09-01"]["outcome"], "ate")
		self.assertEqual(by["2026-09-02"]["outcome"], "missed")
		self.assertEqual(by["2026-09-03"]["status"], "sick")
		self.assertIsNone(by["2026-09-03"]["outcome"])
		self.assertEqual(by["2026-09-04"]["status"], "leave")
		self.assertEqual(by["2026-09-05"]["outcome"], "today")
		self.assertEqual(by["2026-09-06"]["status"], "off_day")
		self.assertTrue(by["2026-09-06"]["ate"])
		self.assertEqual(by["2026-09-07"]["outcome"], "upcoming")
		s = rec["summary"]
		self.assertEqual((s["expected"], s["ate_expected"], s["missed"]), (3, 1, 1))
		self.assertEqual((s["sick"], s["leave"], s["off_day"], s["ate"]), (1, 1, 0, 1))

	def test_off_day_wins_over_leave_and_before_joining_is_not_counted(self):
		d = self.d
		rec = self.ca.employee_days(
			d(1),
			d(3),
			d(10),
			off={d(2): "Weekly Off"},
			leave={d(2): {"leave_type": "Annual Leave"}},
			ate=set(),
			joined=d(2),
		)
		self.assertEqual([x["status"] for x in rec["days"]], ["not_employed", "off_day", "expected"])
		self.assertEqual(rec["summary"]["missed"], 1)


class TestDistinctTotals(unittest.TestCase):
	def test_people_are_distinct_across_days_not_summed(self):
		from upande_ta.upande_ta.api.canteen_analysis import distinct_totals

		# A scans on three days, B on two, C once: 6 person-days, 3 people
		out = distinct_totals([{"A", "B"}, {"A"}, {"A", "B", "C"}, set()])
		self.assertEqual(out, {"days": 6, "people": 3})

	def test_empty_range(self):
		from upande_ta.upande_ta.api.canteen_analysis import distinct_totals

		self.assertEqual(distinct_totals([]), {"days": 0, "people": 0})
