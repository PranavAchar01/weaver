#!/bin/sh
# Puts the target repo back to its vulnerable starting point (tag: torn).
cd "$(dirname "$0")/../target"
git reset -q --hard torn && git clean -qfd && git log -1 --format="target at %h %s"
