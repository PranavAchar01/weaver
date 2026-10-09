"""Ledgerline: a small order desk for a print shop."""
from flask import Flask, render_template

from config.settings import configure
from db import init_db
from routes.files import bp as files_bp
from routes.orders import bp as orders_bp
from routes.tools import bp as tools_bp
from routes.users import bp as users_bp


def create_app():
    app = Flask(__name__)
    configure(app)
    init_db()
    app.register_blueprint(users_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(tools_bp)

    @app.get("/")
    def index():
        return render_template("index.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(port=5000, debug=True)
