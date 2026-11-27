"""Guards' gate punches all arrive IN; each shift gets a clean IN ... OUT.

Run:  bench --site <site> run-tests --app upande_ta --module upande_ta.upande_ta.test_security_checkin
"""

import datetime
import unittest
from unittest.mock import patch

import frappe

from upande_ta.upande_ta import security_checkin as sc


def _log(name, guard, at, log_type="IN"):
	return frappe._dict(
		name=name, security_guard=guard, time=datetime.datetime.fromisoformat(at), log_type=log_type
	)


class TestSecurityCheckinDirections(unittest.TestCase):
	def _run(self, logs, assignments=(), target="2026-11-27"):
		state = {log.name: log.log_type for log in logs}
		calls = []

		def get_all(doctype, **kw):
			if doctype == "Security Checkin":
				return [frappe._dict(log) for log in logs]
			return [frappe._dict(a) for a in assignments]

		def set_value(dt, name, field, value, **kw):
			calls.append((name, value))
			state[name] = value

		with (
			patch.object(sc.frappe.db, "exists", return_value=True),
			patch.object(sc.frappe, "get_all", side_effect=get_all),
			patch.object(sc.frappe.db, "get_value", side_effect=lambda dt, n, f: state[n]),
			patch.object(sc.frappe.db, "set_value", side_effect=set_value),
			patch.object(sc.frappe.db, "commit"),
		):
			out = sc.normalize_security_checkin_directions(target_date=target, days=1)
		return out, state, calls

	def test_day_of_all_ins_closes_with_its_last_scan(self):
		logs = [
			_log("a", "SG-109", "2026-11-27 06:12:00"),
			_log("b", "SG-109", "2026-11-27 08:18:00"),
			_log("c", "SG-109", "2026-11-27 13:04:00"),
		]
		out, state, _c = self._run(logs)
		self.assertEqual(state, {"a": "IN", "b": "IN", "c": "OUT"})
		self.assertEqual(out["flipped_to_out"], 1)

	def test_lone_scan_is_left_alone(self):
		_o, state, calls = self._run([_log("a", "SG-111", "2026-11-27 14:02:00")])
		self.assertEqual((state, calls), ({"a": "IN"}, []))

	def test_all_outs_open_with_the_first_scan(self):
		logs = [
			_log("a", "SG-1", "2026-11-27 06:00:00", "OUT"),
			_log("b", "SG-1", "2026-11-27 18:00:00", "OUT"),
		]
		_o, state, _c = self._run(logs)
		self.assertEqual(state, {"a": "IN", "b": "OUT"})

	def test_night_shift_keeps_both_sides_of_midnight_together(self):
		logs = [_log("a", "SG-2", "2026-11-27 18:40:00"), _log("b", "SG-2", "2026-11-28 06:05:00")]
		shift = {
			"name": "SGSA-1",
			"external_guard": "SG-2",
			"start_date": datetime.date(2026, 11, 27),
			"start_time": datetime.timedelta(hours=19),
			"end_date": datetime.date(2026, 11, 28),
			"end_time": datetime.timedelta(hours=7),
		}
		_o, state, _c = self._run(logs, [shift])
		self.assertEqual(state, {"a": "IN", "b": "OUT"})

	def test_guards_are_kept_apart(self):
		logs = [_log("a", "SG-1", "2026-11-27 06:00:00"), _log("b", "SG-2", "2026-11-27 13:00:00")]
		_o, _s, calls = self._run(logs)
		self.assertEqual(calls, [])
