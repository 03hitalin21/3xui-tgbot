import os


def auth_ok(req) -> bool:
    expected = os.getenv("ADMIN_WEB_TOKEN", "")
    provided = req.args.get("token") or req.form.get("token")
    return bool(expected) and provided == expected
