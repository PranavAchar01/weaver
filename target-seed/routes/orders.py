from flask import Blueprint, jsonify, request

from db import connect

bp = Blueprint("orders", __name__)


@bp.get("/orders")
def list_orders():
    customer = request.args.get("customer", "")
    conn = connect()
    rows = conn.execute(
        "SELECT id, customer, item, status FROM orders WHERE customer = ?", (customer,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
