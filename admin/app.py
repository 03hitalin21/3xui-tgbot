import os

from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader

import db
from admin.routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = os.getenv("ADMIN_WEB_SECRET", "dev-secret")
    root_templates = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader(root_templates)])
    register_routes(app)
    return app


def main() -> None:
    db.init_db()
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.getenv("ADMIN_WEB_PORT", "8080")))
