#!/bin/sh
# Puts the target repo back to its vulnerable starting point (tag: torn).
# Builds ./target from ./target-seed the first time.
cd "$(dirname "$0")/.."
if [ ! -d target ]; then
  cp -R target-seed target && cd target && git init -q && git add -A \
    && git -c user.name="dev" -c user.email="dev@ledgerline.test" commit -q -m "ledgerline: initial order desk" && git tag torn && cd ..
fi
cd target && git reset -q --hard torn && git clean -qfd && git log -1 --format="target at %h %s"
