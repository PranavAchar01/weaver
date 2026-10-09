UPLOAD_LIMIT_MB = 8


def configure(app):
    app.config["SECRET_KEY"] = "ledgerline-9f2c71d0b4a84e6fa1c3"
    app.config["MAX_CONTENT_LENGTH"] = UPLOAD_LIMIT_MB * 1024 * 1024
    return app
