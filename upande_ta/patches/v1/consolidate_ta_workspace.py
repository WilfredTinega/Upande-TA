# Copyright (c) 2026, Upande LTD and contributors
"""Retired.

This patch used to delete every Upande-TA-owned Workspace and re-create it from
a shipped JSON on disk. That job now belongs to
`upande_ta.migrate.resync_app_resources`, which force-imports the shipped nav on
every migrate without the delete-then-recreate round trip.

Kept as a no-op because the name is already recorded in patches.txt on
existing sites; removing the entry would not un-run it there.
"""


def execute():
	pass
