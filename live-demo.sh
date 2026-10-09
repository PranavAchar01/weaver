#!/bin/zsh
# Weaver, end to end on a fresh open source repo: fork, clone, scan, mend, verify, open the pull request.
# Usage: ./live-demo.sh            (needs: gh logged in, semgrep, uv, claude)
set -e
UP=mathandy/svgpathtools
ME=$(gh api user --jq .login)
WEAVER=${WEAVER:-$HOME/helloworld/weaver}
PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
WORK=$(mktemp -d)/svgpathtools
BR=weaver/mend-$(date +%H%M%S)
T0=$(date +%s)

echo "weaver-demo: forking $UP to $ME (pull request stays in the fork, nothing goes upstream)"
gh repo fork $UP --clone=false >/dev/null 2>&1 || true
until gh repo view $ME/svgpathtools >/dev/null 2>&1; do sleep 2; done
git clone -q https://github.com/$ME/svgpathtools $WORK
cd $WORK
BASE=$(git rev-parse --abbrev-ref HEAD)
START=$(git rev-parse HEAD)
mkdir -p .weaver
# marked by hand: these three only import XML types or re-read a file the library just wrote
printf 'svgpathtools/parser.py\nsvgpathtools/paths2svg.py\nsvgpathtools/svg_io_sax.py\nexamples\ntest\n' > .weaver/skip

(cd $WEAVER && exec uv run weaver watch $WORK --lang python --no-tests --port $PORT) &
PID=$!
# the open browser tab holds a stream, so the server ignores a plain kill: ask, then force
stop() { kill $PID 2>/dev/null; sleep 2; pkill -9 -P $PID 2>/dev/null; kill -9 $PID 2>/dev/null; true; }
trap stop EXIT
sleep 4
open "http://127.0.0.1:$PORT"

# done when at least one mend is committed and the event log has been quiet for 20 s
last=0; quiet=0
while :; do
  sleep 5
  lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null || { echo "weaver-demo: the Weaver server is not running, stopping"; exit 1; }
  n=$( [ -f .weaver/events.jsonl ] && wc -l < .weaver/events.jsonl || echo 0)
  mends=$(git rev-list --count $START..HEAD)
  if [ "$n" = "$last" ]; then quiet=$((quiet+5)); else quiet=0; last=$n; fi
  echo "weaver-demo: $mends mend commits, $n events"
  [ $mends -ge 1 ] && [ $quiet -ge 20 ] && break
  [ $(( $(date +%s) - T0 )) -gt 600 ] && { echo "weaver-demo: timed out"; break; }
done
stop
[ $(git rev-list --count $START..HEAD) -ge 1 ] || { echo "weaver-demo: nothing was mended, no pull request"; exit 1; }

if git diff $START..HEAD | grep -q '^+.*defusedxml' && ! grep -q defusedxml requirements.txt; then
  echo defusedxml >> requirements.txt
  sed -i '' "s/install_requires=\['numpy', 'svgwrite', 'scipy'\]/install_requires=['numpy', 'svgwrite', 'scipy', 'defusedxml']/" setup.py
  git commit -qam "Add defusedxml dependency"
fi
git switch -q -c $BR
git push -q origin $BR
SECS=$(( $(date +%s) - T0 ))
BODY="Semgrep flagged XML parsing of user supplied SVG files with the standard library parser, which is open to entity expansion attacks. Weaver patched each finding, then verified by rescanning with Semgrep and compiling. The project's tests were not run.

Files changed:
$(git diff --stat $START..HEAD | sed 's/^/    /')

Commits:
$(git log --format='- %s' $START..HEAD)

Opened by Weaver (github.com/PranavAchar01/weaver), $SECS s from fork to pull request."
URL=$(gh pr create -R $ME/svgpathtools -B $BASE -H $BR -t "Parse SVG input with defusedxml" -b "$BODY")
echo "weaver-demo: $URL  ($SECS s)"
open "$URL"
