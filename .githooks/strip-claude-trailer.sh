#!/usr/bin/env bash
# Strip Claude self-attribution from a commit message.
#
# Two modes, same rules:
#   strip-claude-trailer.sh <file>   edit that commit-message file in place
#                                    (pre-commit's `commit-msg` stage passes it)
#   strip-claude-trailer.sh          filter stdin -> stdout
#                                    (`git filter-branch --msg-filter`)
#
# Removes:
#   Co-Authored-By: Claude ... <noreply@anthropic.com>
#   🤖 Generated with [Claude Code](...)
# Co-authored-by trailers for human beings are left alone. Trailing blank lines
# left behind by the deletion are collapsed to a single closing newline.
set -euo pipefail

filter() {
	local out
	out="$(sed \
		-e '/^[[:space:]]*Co-[Aa]uthored-[Bb]y:[[:space:]]*Claude/d' \
		-e '/Generated with \[Claude Code\]/d')"
	printf '%s\n' "$out"
}

if [ "$#" -eq 0 ]; then
	filter
	exit 0
fi

msg_file="$1"
[ -f "$msg_file" ] || exit 0

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
filter <"$msg_file" >"$tmp"

if ! cmp -s "$tmp" "$msg_file"; then
	cat "$tmp" >"$msg_file"
	echo "strip-claude-trailer: removed Claude attribution from the commit message"
fi
