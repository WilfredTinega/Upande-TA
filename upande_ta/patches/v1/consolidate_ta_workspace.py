# Copyright (c) 2026, Upande LTD and contributors
"""Retired.

This patch used to delete every Workspace owned by this app and re-create it
from the shipped JSON. That destroys whatever the site had added to it -- the
card layout, shortcuts, embedded blocks -- and it ran ahead of
`rename_ta_workspace_to_upande_ta` in patches.txt, so on any site that had not
run it yet it wiped the workspace before the rename could carry those children
across. The rename is the only thing that should touch that record now.

Kept as a no-op rather than removed: the name is already in the Patch Log on
sites that ran it, and deleting the entry from patches.txt would not un-run it
there while a fresh site would simply never see it.
"""


def execute():
	pass
