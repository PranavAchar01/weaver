#!/bin/sh
# Lands one new commit in the target repo that introduces a SQL injection.
# Weaver is not told about it; the watcher has to notice.
cd "$(dirname "$0")/../target"
python3 - <<'PY'
import pathlib
p = pathlib.Path("routes/orders.py")
s = p.read_text()
old = s[s.index("    customer = request.args"):s.index("    conn.close()")]
new = '''    customer = request.args.get("customer", "")
    status = request.args.get("status", "")
    conn = connect()
    query = "SELECT id, customer, item, status FROM orders WHERE customer = '" + customer + "'"
    if status:
        query += " AND status = '" + status + "'"
    rows = conn.execute(query).fetchall()
'''
p.write_text(s.replace(old, new))
PY
git add routes/orders.py
git -c user.name="dev" -c user.email="dev@ledgerline.test" commit -q -m "orders: add status filter"
git log -1 --format="%h %s"
