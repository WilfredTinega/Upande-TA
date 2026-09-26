# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Uniqueness, multi-signature and anomaly rules of the Biometric Templates
panel. Lives here because frappe only honours IGNORE_TEST_RECORD_DEPENDENCIES
in a doctype folder; the code under test is
upande_ta.upande_ta.api.biometric_templates."""

import datetime
import unittest

import frappe

IGNORE_TEST_RECORD_DEPENDENCIES = ["Employee", "User", "Company", "Holiday List"]

D = datetime.date


def emp(name, **kw):
	row = {
		"name": name,
		"employee_name": "Name " + name,
		"status": "Active",
		"company": "Co A",
		"farm": "Unit 1",
		"department": "",
		"designation": "",
		"employment_type": "",
		"holiday_list": "",
		"date_of_joining": None,
	}
	row.update(kw)
	return frappe._dict(row)


def bio(device, employee, face=None, palm=None, fp=None, uid="", deleted=0):
	return {
		"device": device,
		"employee": employee,
		"employee_name": "",
		"user_id": uid,
		"deleted": deleted,
		"face_sig": face,
		"palm_sig": palm,
		"fp_sig": fp,
	}


READERS = [
	{"name": "R1", "device_location": "Gate", "device_sn": "SN1"},
	{"name": "R2", "device_location": "Canteen", "device_sn": "SN2"},
]


class TestEnrolment(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api import biometric_templates as bt

		self.bt = bt

	def judge(self, active, rows, inactive=None, names=None, **kw):
		names = names if names is not None else {e.name: e for e in active + list((inactive or {}).values())}
		return self.bt.judge_enrolment(active, inactive or {}, READERS, rows, names, **kw)

	def by_id(self, res):
		return {t["e"]: t for t in res["templates"]}

	def test_same_template_everywhere_is_unique(self):
		res = self.judge([emp("E1")], [bio("R1", "E1", face="a" * 32), bio("R2", "E1", face="a" * 32)])
		t = self.by_id(res)["E1"]
		self.assertEqual(t["st"], "Unique")
		self.assertEqual(t["dc"], 2)
		self.assertEqual(t["gaps"], [])
		self.assertEqual(t["seats"], [[0, "a" * 10], [1, "a" * 10]])
		self.assertEqual(res["summary"]["unique"], 1)

	def test_shared_blob_is_duplicate_on_both_sides(self):
		res = self.judge([emp("E1"), emp("E2")], [bio("R1", "E1", fp="f" * 32), bio("R2", "E2", fp="f" * 32)])
		t = self.by_id(res)
		self.assertEqual(t["E1"]["st"], "Duplicate")
		self.assertEqual(t["E2"]["st"], "Duplicate")
		self.assertEqual(t["E1"]["sw"], [["E2", "Name E2"]])
		self.assertEqual(len(res["collisions"]), 1)
		self.assertEqual(res["collisions"][0]["kind"], "Fingerprint")

	def test_user_id_clash_is_duplicate(self):
		res = self.judge(
			[emp("E1"), emp("E2")],
			[bio("R1", "E1", face="1" * 32, uid="7"), bio("R1", "E2", face="2" * 32, uid="7")],
		)
		t = self.by_id(res)
		self.assertEqual(t["E1"]["st"], "Duplicate")
		self.assertEqual(t["E1"]["su"], [["E2", "Name E2"]])
		self.assertEqual(res["collisions"], [])

	def test_face_and_palm_is_not_multi_signature(self):
		res = self.judge([emp("E1")], [bio("R1", "E1", face="a" * 32), bio("R2", "E1", palm="b" * 32)])
		t = self.by_id(res)["E1"]
		self.assertEqual(t["st"], "Unique")
		self.assertEqual(t["sc"], 2)
		self.assertEqual(sorted(t["kinds"]), ["Face", "Palm"])

	def test_two_faces_is_multi_signature(self):
		res = self.judge([emp("E1")], [bio("R1", "E1", face="a" * 32), bio("R2", "E1", face="c" * 32)])
		t = self.by_id(res)["E1"]
		self.assertEqual(t["st"], "Multi-signature")
		self.assertEqual(t["vk"], ["Face"])

	def test_deleted_rows_do_not_count(self):
		res = self.judge(
			[emp("E1"), emp("E2")],
			[bio("R1", "E1", face="a" * 32), bio("R2", "E2", face="a" * 32, deleted=1)],
		)
		t = self.by_id(res)
		self.assertEqual(t["E1"]["st"], "Unique")
		self.assertEqual(t["E2"]["st"], "Not enrolled")
		self.assertEqual(t["E2"]["rm"], 1)
		self.assertEqual(res["not_enrolled"], ["E2"])

	def test_partial_coverage(self):
		res = self.judge([emp("E1")], [bio("R1", "E1", face="a" * 32)])
		self.assertEqual(res["partly_enrolled"], ["E1"])
		self.assertEqual(self.by_id(res)["E1"]["gaps"], [1])
		self.assertEqual(res["devices"][1]["not_enrolled"], 1)

	def test_inactive_live_enrolment_is_orphan(self):
		gone = emp("E9", status="Left")
		res = self.judge([emp("E1")], [bio("R2", "E9", face="z" * 32)], inactive={"E9": gone})
		self.assertEqual(len(res["orphans"]), 1)
		self.assertEqual(res["orphans"][0]["reader"], 1)
		self.assertEqual(res["orphans"][0]["status"], "Left")
		self.assertEqual(res["devices"][1]["inactive_enrolled"], 1)

	def test_active_employee_outside_scope_is_not_orphan(self):
		other = emp("E5", company="Co B")
		res = self.judge([emp("E1")], [bio("R1", "E5", face="z" * 32)], names={"E1": emp("E1"), "E5": other})
		self.assertEqual(res["orphans"], [])

	def test_collision_name_hidden_outside_visible_companies(self):
		other = emp("E5", company="Co B")
		res = self.judge(
			[emp("E1")],
			[bio("R1", "E1", face="q" * 32), bio("R2", "E5", face="q" * 32)],
			names={"E1": emp("E1"), "E5": other},
			visible_companies={"Co A"},
		)
		t = self.by_id(res)["E1"]
		self.assertEqual(t["st"], "Duplicate")
		self.assertEqual(t["sw"], [["E5", ""]])


class TestAnomalies(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api import biometric_templates as bt

		self.bt = bt
		self.people = {
			"E1": emp("E1", holiday_list="HL"),
			"E2": emp("E2", holiday_list="HL", date_of_joining=D(2026, 1, 3)),
			"E3": emp("E3", status="Left", holiday_list="HL"),
		}
		self.start, self.end = D(2026, 1, 1), D(2026, 1, 4)

	def punch(self, eid, day, n=2):
		return {
			"employee": eid,
			"pdate": day,
			"first_in": datetime.datetime.combine(day, datetime.time(7, 5)),
			"last_out": datetime.datetime.combine(day, datetime.time(17, 0)),
			"punches": n,
			"devices": "SN1",
		}

	def run_(self, punches, off_days=None, leaves=None, marked=None, enrolled=("E1", "E2")):
		return self.bt.find_anomalies(
			people=self.people,
			enrolled=set(enrolled),
			punches=punches,
			off_days=off_days or {},
			leaves=leaves or {},
			marked=marked or {},
			start=self.start,
			end=self.end,
		)

	def test_off_day_punch(self):
		off = {"E1": {D(2026, 1, 2): {"weekly_off": 1, "holiday_list": "HL"}}}
		res = self.run_([self.punch("E1", D(2026, 1, 2))], off_days=off)
		self.assertEqual(len(res["off_day"]), 1)
		row = res["off_day"][0]
		self.assertEqual(
			(row["type"], row["why"], row["in"], row["out"]), ("Off day", "Week Off", "07:05", "17:00")
		)

	def test_public_holiday_reason(self):
		off = {"E1": {D(2026, 1, 1): {"weekly_off": 0, "description": "New Year", "holiday_list": "HL"}}}
		res = self.run_([self.punch("E1", D(2026, 1, 1))], off_days=off)
		self.assertEqual(res["off_day"][0]["why"], "New Year")

	def test_on_leave_punch(self):
		lv = {
			"E1": [
				{
					"name": "LA-1",
					"leave_type": "Annual",
					"from_date": D(2026, 1, 2),
					"to_date": D(2026, 1, 3),
					"half_day": 1,
				}
			]
		}
		res = self.run_([self.punch("E1", D(2026, 1, 3))], leaves=lv)
		self.assertEqual(len(res["on_leave"]), 1)
		self.assertEqual(res["on_leave"][0]["why"], "Annual (half day)")
		self.assertEqual(res["on_leave"][0]["doc"], "LA-1")

	def test_inactive_punch_still_flagged(self):
		off = {"E3": {D(2026, 1, 2): {"weekly_off": 1, "holiday_list": "HL"}}}
		res = self.run_([self.punch("E3", D(2026, 1, 2))], off_days=off)
		self.assertEqual(res["off_day"][0]["active"], 0)

	def test_enrolled_but_absent(self):
		off = {"E1": {D(2026, 1, 4): {"weekly_off": 1, "holiday_list": "HL"}}}
		lv = {
			"E1": [
				{
					"name": "LA-2",
					"leave_type": "Sick",
					"from_date": D(2026, 1, 2),
					"to_date": D(2026, 1, 2),
					"half_day": 0,
				}
			]
		}
		res = self.run_(
			[self.punch("E1", D(2026, 1, 1))],
			off_days=off,
			leaves=lv,
			marked={("E1", D(2026, 1, 3)): "Absent"},
			enrolled=("E1",),
		)
		# 1st punched, 2nd on leave, 4th off -> only the 3rd
		self.assertEqual(res["absent"], [["E1", "2026-01-03", "Absent"]])

	def test_absent_skips_before_joining_and_unenrolled(self):
		res = self.run_([], enrolled=("E2",))
		self.assertEqual([r[1] for r in res["absent"]], ["2026-01-03", "2026-01-04"])
		res = self.run_([], enrolled=())
		self.assertEqual(res["absent"], [])

	def test_absent_skips_inactive(self):
		res = self.run_([], enrolled=("E3",))
		self.assertEqual(res["absent"], [])


class TestOffDayResolution(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api import biometric_templates as bt

		self.bt = bt

	def test_assignment_wins_then_employee_field(self):
		people = [emp("E1", holiday_list="OLD")]
		ranges = {"E1": [{"holiday_list": "NEW", "from_date": D(2026, 1, 3), "to_date": D(2026, 1, 4)}]}
		holidays = {
			"OLD": {D(2026, 1, 1): {"weekly_off": 1}, D(2026, 1, 3): {"weekly_off": 1}},
			"NEW": {D(2026, 1, 4): {"weekly_off": 1}},
		}
		out = self.bt.resolve_off_days(people, ranges, holidays, D(2026, 1, 1), D(2026, 1, 4))
		# Jan 1 by the employee field, Jan 3 is NOT off (NEW in force), Jan 4 off by NEW
		self.assertEqual(sorted(out["E1"]), [D(2026, 1, 1), D(2026, 1, 4)])
		self.assertEqual(out["E1"][D(2026, 1, 4)]["holiday_list"], "NEW")


class TestWindowAndGate(unittest.TestCase):
	def setUp(self):
		from upande_ta.upande_ta.api import biometric_templates as bt

		self.bt = bt

	def test_clamp(self):
		s, e, n, c = self.bt.clamp_window("2026-01-10", "2026-01-01")
		self.assertEqual((s, e, n, c), (D(2026, 1, 1), D(2026, 1, 10), 10, 0))
		s, e, n, c = self.bt.clamp_window("2025-01-01", "2026-01-31")
		self.assertEqual((n, c, e), (self.bt.MAX_DAYS, 1, D(2026, 1, 31)))
		self.assertEqual((e - s).days + 1, self.bt.MAX_DAYS)

	def test_endpoint_shape_as_administrator(self):
		frappe.set_user("Administrator")
		res = self.bt.biometric_templates(from_date="2026-01-01", to_date="2026-01-02")
		for key in ("templates", "devices", "anomalies", "summary", "orphans", "companies"):
			self.assertIn(key, res)
		for key in ("off_day", "on_leave", "absent"):
			self.assertIn(key, res["anomalies"])
		s = res["summary"]
		self.assertEqual(s["enrolled"] + s["not_enrolled"], s["employees"])
		self.assertEqual(s["unique"] + s["duplicate"] + s["multi_signature"], s["enrolled"])

	def test_guest_is_refused(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				self.bt.biometric_templates()
		finally:
			frappe.set_user("Administrator")
