import os
import subprocess

from flask import Blueprint, jsonify, request

bp = Blueprint("tools", __name__)

PROOFS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "proofs")


@bp.get("/tools/linecount")
def linecount():
    name = request.args.get("name", "")
    out = subprocess.check_output("wc -l " + name, shell=True, cwd=PROOFS_DIR, text=True)
    return jsonify({"lines": int(out.split()[0]), "raw": out})
