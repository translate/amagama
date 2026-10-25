#!/bin/bash
# Pre-commit hook: every Python file and bin/ script carries the header
# in .license.header.txt, with a shebang above it only on the scripts in
# bin/. docs/conf.py is left out: its own descriptive comment starts the
# file.
set -eu

HEADER='#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.'

failed=()
for f in "$@"; do
    [ -f "$f" ] || continue
    case "$f" in
        docs/conf.py) continue ;;
    esac
    first=1
    if head -1 "$f" | grep -q '^#!'; then
        case "$f" in
            bin/*) first=2 ;;
            *) failed+=("$f (a shebang outside bin/)"); continue ;;
        esac
    fi
    if [ "$(sed -n "${first},$((first + 5))p" "$f")" != "$HEADER" ]; then
        failed+=("$f")
    fi
done

if [ "${#failed[@]}" -gt 0 ]; then
    echo "These files don't start with the copyright header in .license.header.txt:" >&2
    printf '  %s\n' "${failed[@]}" >&2
    exit 1
fi
