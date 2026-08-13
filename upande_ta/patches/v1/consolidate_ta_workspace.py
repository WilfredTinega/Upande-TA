# Copyright (c) 2026, Upande LTD and contributors
"""Retired.

This patch used to delete every Upande-TA-owned Workspace and re-create it
from a shipped JSON on disk. The app no longer owns the nav records (Desktop
Icon / Workspace Sidebar / Workspace) -- they are site data, maintained in the
Desk UI -- so re-importing them on migrate would clobber local edits.

Kept as a no-op because the name is already recorded in patches.txt on
existing sites; removing the entry would not un-run it there.
"""


def execute():
	pass
