# Copyright (c) 2026, Upande LTD and Contributors
# See license.txt

"""Unit tests for ``upande_ta.upande_ta.overtime_engine`` — the arithmetic
Bulk Overtime pays by. Site-free: plain ``unittest``, no database, so they run
under ``bench run-tests`` and under a bare ``python -m unittest`` alike.

The end-to-end path (request -> Bulk Overtime -> Overtime Slip) is in
``test_bulk_overtime_end_to_end.py``.
"""

import datetime
import unittest

from upande_ta.upande_ta import overtime_engine as engine
from upande_ta.upande_ta.overtime_engine import (
	CAPPED,
	MATCHED,
	NO_ATTENDANCE,
	NO_CLOCK_OUT,
	NO_SHIFT,
	PUBLIC_HOLIDAY,
	REST_DAY,
	WORKED_LESS,
	WORKING_DAY,
	biometric_overtime,
	settle,
	shift_length_hours,
)

# Frappe crawls Link targets for test records unless told not to; this module
# needs none of them.
#: Generating a "_Test Company" pulls in erpnext's country fixtures, which this
#: site cannot install and none of these tests need.
IGNORE_TEST_RECORD_DEPENDENCIES = [
	"Additional Salary",
	"Attendance",
	"Company",
	"Department",
	"Designation",
	"Employee",
	"Farm",
	"Gender",
	"Holiday List",
	"Overtime Request",
	"Overtime Slip",
	"Overtime Type",
	"Salary Component",
	"Salary Structure",
	"Shift Type",
]


class TestShiftLength(unittest.TestCase):
	def test_day_shift(self):
		# KR - Post Harvest: 07:45 -> 16:40
		self.assertAlmostEqual(
			shift_length_hours(datetime.timedelta(hours=7, minutes=45), datetime.timedelta(hours=16, minutes=40)),
			8.9167,
			places=3,
		)

	def test_night_shift_wraps_midnight(self):
		self.assertEqual(shift_length_hours(datetime.timedelta(hours=22), datetime.timedelta(hours=6)), 8)

	def test_accepts_time_and_strings(self):
		self.assertEqual(shift_length_hours(datetime.time(8), "17:00:00"), 9)

	def test_missing_or_zero_length_is_none(self):
		self.assertIsNone(shift_length_hours(None, datetime.time(17)))
		self.assertIsNone(shift_length_hours("08:00", "08:00"))


class TestBiometricOvertime(unittest.TestCase):
	def test_working_day_counts_hours_beyond_the_shift(self):
		self.assertEqual(biometric_overtime(11, 9, WORKING_DAY), 2)

	def test_working_day_under_the_shift_is_zero(self):
		self.assertEqual(biometric_overtime(7.5, 9, WORKING_DAY), 0)

	def test_rest_day_and_holiday_count_every_hour(self):
		self.assertEqual(biometric_overtime(6, 9, REST_DAY), 6)
		self.assertEqual(biometric_overtime(6, 9, PUBLIC_HOLIDAY), 6)

	def test_part_hours_are_kept(self):
		self.assertEqual(biometric_overtime(9.5, 9, WORKING_DAY), 0.5)
		self.assertEqual(biometric_overtime(9 + 25 / 60, 9, WORKING_DAY), 0.42)

	def test_daily_cap_from_the_overtime_type(self):
		"""Maximum Overtime Hours Allowed on the Overtime Type; 0 means no cap."""
		self.assertEqual(biometric_overtime(14, 8, WORKING_DAY, maximum_hours=4), 4)
		self.assertEqual(biometric_overtime(14, 8, WORKING_DAY, maximum_hours=0), 6)

	def test_bad_working_hours_are_zero(self):
		self.assertEqual(biometric_overtime(-3, 8, REST_DAY), 0)
		self.assertEqual(biometric_overtime(None, 8, REST_DAY), 0)

	def test_unknown_day_type_is_an_error(self):
		with self.assertRaises(ValueError):
			biometric_overtime(10, 8, "Sunday")


class TestSettle(unittest.TestCase):
	"""The pay rule: the lower of requested and biometric."""

	def settle(self, requested, biometric, attendance=True, hours=True, shift=True):
		return settle(
			requested, biometric, has_attendance=attendance, has_hours=hours, has_shift=shift
		)

	def test_worked_more_than_requested_is_capped(self):
		self.assertEqual(self.settle(2, 3.5), (2, CAPPED))

	def test_worked_less_pays_what_was_worked(self):
		self.assertEqual(self.settle(3, 1.5), (1.5, WORKED_LESS))

	def test_exact(self):
		self.assertEqual(self.settle(2, 2), (2, MATCHED))

	def test_no_overtime_worked(self):
		self.assertEqual(self.settle(2, 0), (0, WORKED_LESS))

	def test_no_attendance_pays_nothing(self):
		self.assertEqual(self.settle(2, 0, attendance=False, hours=False), (0, NO_ATTENDANCE))

	def test_missing_clock_out_pays_nothing(self):
		self.assertEqual(self.settle(2, 0, attendance=True, hours=False), (0, NO_CLOCK_OUT))

	def test_no_shift_length_pays_nothing(self):
		"""Nothing to measure "beyond the shift" against."""
		self.assertEqual(self.settle(2, 0, shift=False), (0, NO_SHIFT))


class TestEngineIsFrameworkFree(unittest.TestCase):
	def test_no_frappe_import(self):
		import inspect

		self.assertNotIn("import frappe", inspect.getsource(engine))


if __name__ == "__main__":
	unittest.main()


class TestOvertimeWorkflowChain(unittest.TestCase):
	"""The approval chain is built from one config block, so the invariants
	that keep it safe are worth pinning to that block rather than to a
	hand-written list of states."""

	def setUp(self):
		from upande_ta.upande_ta import overtime_workflow as wf

		self.wf = wf

	def test_approved_is_the_only_submitted_state(self):
		"""Bulk Overtime pays from docstatus 1, and a plain submit() lands on
		the first state carrying it."""
		submitted = [s for s in self.wf.states() if s.doc_status == "1"]
		self.assertEqual([s.state for s in submitted], ["Approved"])

		order = [s.state for s in self.wf.states()]
		self.assertLess(order.index("Approved"), order.index("Rejected"))
		self.assertLess(order.index("Approved"), order.index("Cancelled"))

	def test_a_rejection_never_submits(self):
		"""The whole reason Rejected is docstatus 0: at 1 a rejected request
		would be paid, and a rejected batch would cut its slips."""
		self.assertEqual(self.wf.REJECTED.doc_status, "0")

	def test_every_colour_is_one_the_desk_knows(self):
		for stage in self.wf.states():
			with self.subTest(state=stage.state):
				self.assertIn(stage.colour, self.wf.COLOURS)

	def test_the_chain_runs_draft_to_approved(self):
		moves = {(f, a): t for f, a, t, _role in self.wf.transitions()}
		self.assertEqual(moves[("Draft", "Submit for Approval")], "Pending Approval")
		self.assertEqual(moves[("Pending Approval", "Approve")], "Approved")
		self.assertEqual(moves[("Pending Approval", "Reject")], "Rejected")
		self.assertEqual(moves[("Rejected", "Reopen")], "Draft")

	def test_adding_a_stage_rewires_the_chain(self):
		"""Adding a signature should need nothing but another Stage."""
		from unittest.mock import patch

		extra = self.wf.Stage(state="Pending HOD", role="HOD", action="Authorise")
		with patch.object(self.wf, "APPROVALS", (self.wf.APPROVALS[0], extra)):
			moves = {(f, a): t for f, a, t, _role in self.wf.transitions()}
			# the first stage now hands on to the new one, and it to Approved
			self.assertEqual(moves[("Pending Approval", "Approve")], "Pending HOD")
			self.assertEqual(moves[("Pending HOD", "Authorise")], "Approved")
			# and the new stage can reject like any other
			self.assertEqual(moves[("Pending HOD", "Reject")], "Rejected")
			self.assertIn("Authorise", self.wf.actions())

	def test_a_stage_edits_as_its_own_role_unless_told_otherwise(self):
		stage = self.wf.Stage(state="X", role="HR Manager")
		self.assertEqual(stage.editor, "HR Manager")
		self.assertEqual(self.wf.Stage(state="X", role="HR Manager", allow_edit="HR User").editor, "HR User")
