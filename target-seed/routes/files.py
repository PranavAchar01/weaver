import os

from flask import Blueprint, send_from_directory

bp = Blueprint("files", __name__)

PROOFS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "proofs")


@bp.get("/proofs/<name>")
def read_proof(name):
    return send_from_directory(PROOFS_DIR, name, mimetype="text/plain")
