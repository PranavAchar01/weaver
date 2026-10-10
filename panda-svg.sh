#!/bin/zsh
# Weaver, end to end on a fresh open source repo: fork, clone, scan, mend, verify, open the pull request.
# Usage: ./panda-svg.sh            (needs: gh logged in, semgrep, uv, claude)
#        FULL=1 ./panda-svg.sh     also mends document.py (about 2.5 minutes instead of about 1)
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
# document.py has the same real finding but takes the patcher two or three tries; the short run leaves it out
[ -n "$FULL" ] || echo svgpathtools/document.py >> .weaver/skip

(cd $WEAVER && exec uv run weaver watch $WORK --lang python --no-tests --port $PORT) &
PID=$!
# the open browser tab holds a stream, so the server ignores a plain kill: ask, then force
stop() { kill $PID 2>/dev/null; sleep 2; pkill -9 -P $PID 2>/dev/null; kill -9 $PID 2>/dev/null; true; }
trap stop EXIT
until lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null; do sleep 0.3; kill -0 $PID 2>/dev/null || exit 1; done
open "http://127.0.0.1:$PORT"

# done when every finding is mended, or when a mend is committed and the event log has been quiet for 20 s
ev() { local c; c=$(grep -c "\"type\": \"$1\"" .weaver/events.jsonl 2>/dev/null); echo ${c:-0}; }
last=0; quiet=0
while :; do
  sleep 1
  lsof -ti tcp:$PORT -sTCP:LISTEN >/dev/null || { echo "weaver-demo: the Weaver server is not running, stopping"; exit 1; }
  n=$( [ -f .weaver/events.jsonl ] && wc -l < .weaver/events.jsonl || echo 0)
  mends=$(git rev-list --count $START..HEAD)
  if [ "$n" = "$last" ]; then quiet=$((quiet+1)); else quiet=0; last=$n; echo "weaver-demo: $mends mend commits, $n events"; fi
  [ $(ev scan_done) -ge 1 ] && [ $mends -ge 1 ] && [ $(ev mended) -ge $(ev tear) ] && break
  [ $mends -ge 1 ] && [ $quiet -ge 20 ] && break
  [ $(( $(date +%s) - T0 )) -gt 600 ] && { echo "weaver-demo: timed out"; break; }
done
[ $(git rev-list --count $START..HEAD) -ge 1 ] || { echo "weaver-demo: nothing was mended, no pull request"; exit 1; }

if git diff $START..HEAD | grep -q '^+.*defusedxml' && ! grep -q defusedxml requirements.txt; then
  echo defusedxml >> requirements.txt
  sed -i '' "s/install_requires=\['numpy', 'svgwrite', 'scipy'\]/install_requires=['numpy', 'svgwrite', 'scipy', 'defusedxml']/" setup.py
  git commit -qam "Add defusedxml dependency"
fi
git switch -q -c $BR
git push -q origin $BR
SECS=$(( $(date +%s) - T0 ))
BODY="Semgrep flagged XML parsing of user supplied SVG files with the standard library parser, which is open to entity expansion attacks. Weaver patched the files below, then verified by rescanning with Semgrep and compiling. The project's tests were not run.$([ -n "$FULL" ] || echo ' svgpathtools/document.py has the same finding and is not part of this pull request.')

Files changed:
$(git diff --stat $START..HEAD | sed 's/^/    /')

Commits:
$(git log --format='- %s' $START..HEAD)

Opened by Weaver (github.com/PranavAchar01/weaver), $SECS s from fork to pull request."
URL=$(gh pr create -R $ME/svgpathtools -B $BASE -H $BR -t "Parse SVG input with defusedxml" -b "$BODY")
echo "weaver-demo: $URL  ($SECS s)"
open "$URL"
# the server is still writing the Semgrep rule for the mend; give it a moment before the trap stops it
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do [ $(ev rule_written) -ge 1 ] || [ $(ev rule_discarded) -ge 1 ] && break; sleep 1; done
