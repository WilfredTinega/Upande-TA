# Copyright (c) 2026, Upande LTD and contributors

"""Monthly Attendance Report — the same report as HRMS' Monthly Attendance Sheet.

Both names exist for historical reasons (the T&A workspace links to "Monthly
Attendance Report"), and this report exists so the two render identically instead
of being two separately maintained implementations.

It deliberately contains no report logic of its own: ``execute`` delegates to
upande_ta's patched Monthly Attendance Sheet implementation
(``upande_ta/upande_ta/overrides/monthly_attendance_sheet.py``), which itself
wraps hrms' original ``execute``. Everything the override adds is therefore
shared, not duplicated:

* per-date holiday resolution via Holiday List Assignments
* one row per employee for the whole period, labelled with the shift they worked
  the most days in
* per-leave-type abbreviations from Leave Type (instead of a generic "L")
* the "Category" (Employee Grade) filter
* the appended Present/Absent/On Leave/... summary block

The client side is mirrored the same way — see
``upande_ta/public/js/monthly_attendance_sheet_colors.bundle.js``, which loads the
sheet's filter list at runtime rather than re-declaring it here.
"""

from upande_ta.upande_ta.overrides import monthly_attendance_sheet as mas


def execute(filters=None):
	# apply_patch() is idempotent and normally already ran on before_request /
	# before_job. Call it here as well so the report still works from a context
	# that never went through those hooks (bench execute, tests, a worker that
	# imported this module before the patch was applied). mas.execute() also
	# self-heals, but doing it here keeps the failure mode obvious.
	mas.apply_patch()
	return mas.execute(filters)
