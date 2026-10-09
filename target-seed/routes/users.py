from flask import Blueprint, jsonify, request

from db import connect

bp = Blueprint("users", __name__)


@bp.get("/users")
def find_users():
    name = request.args.get("name", "")
    conn = connect()
    rows = conn.execute("SELECT id, name, email FROM users WHERE name = '" + name + "'").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])
