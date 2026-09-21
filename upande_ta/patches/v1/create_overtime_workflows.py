# Copyright (c) 2026, Upande LTD and contributors
"""Create the "Overtime Request Approval" and "Bulk Overtime Approval" workflows.

The chain itself — the stages, who approves at each one, the colours and who
may edit where — lives in :mod:`upande_ta.upande_ta.overtime_workflow`, which
is where to change it. This only applies it on migrate, and only where the
workflow is missing, so edits made in the Desk UI survive.

After changing the roles there, re-apply with::

    bench --site <site> execute upande_ta.upande_ta.overtime_workflow.rebuild
"""

from upande_ta.upande_ta.overtime_workflow import setup


def execute():
	setup()
