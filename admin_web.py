import os
from flask import Flask, redirect, render_template_string, request, url_for

import db

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_WEB_SECRET", "dev-secret")


def auth_ok(req) -> bool:
    expected = os.getenv("ADMIN_WEB_TOKEN", "")
    provided = req.args.get("token") or req.form.get("token")
    return bool(expected) and provided == expected


PAGE = """
<!doctype html>
<title>Bot Admin</title>
<style>
body{font-family:Arial;max-width:1000px;margin:20px auto;padding:0 12px}
.card{border:1px solid #ddd;border-radius:8px;padding:12px;margin-bottom:12px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;font-size:13px}
input{padding:6px;margin:4px}
</style>
<h1>Bot Admin Panel</h1>
<div class='card'>
  <h3>Pricing</h3>
  <form method='post' action='/pricing'>
    <input type='hidden' name='token' value='{{token}}'>
    Price/GB: <input name='price_per_gb' value='{{price_per_gb}}'>
    Price/Day: <input name='price_per_day' value='{{price_per_day}}'>
    <button>Save</button>
  </form>
</div>
<div class='card'>
  <h3>Create Promo Code</h3>
  <form method='post' action='/promo'>
    <input type='hidden' name='token' value='{{token}}'>
    Code: <input name='code' placeholder='NEWYEAR'>
    Discount %: <input name='discount_percent' placeholder='20'>
    Max uses (optional): <input name='max_uses' placeholder='100'>
    <button>Create</button>
  </form>
  <h4>Promo List</h4>
  <table><tr><th>Code</th><th>%</th><th>Used</th><th>Max</th><th>Active</th></tr>
  {% for p in promos %}<tr><td>{{p['code']}}</td><td>{{p['discount_percent']}}</td><td>{{p['used_count']}}</td><td>{{p['max_uses']}}</td><td>{{p['active']}}</td></tr>{% endfor %}
  </table>
</div>
<div class='card'>
  <h3>Agent performance</h3>
  <table><tr><th>TG ID</th><th>User</th><th>Name</th><th>Balance</th><th>Lifetime Topup</th><th>Clients</th><th>Spent</th></tr>
  {% for a in agents %}<tr><td>{{a['tg_id']}}</td><td>{{a['username']}}</td><td>{{a['full_name']}}</td><td>{{a['balance']}}</td><td>{{a['lifetime_topup']}}</td><td>{{a['clients']}}</td><td>{{a['spent']}}</td></tr>{% endfor %}
  </table>
</div>
"""


@app.get("/")
def index():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    return render_template_string(
        PAGE,
        token=request.args.get("token"),
        price_per_gb=db.get_setting_float("price_per_gb"),
        price_per_day=db.get_setting_float("price_per_day"),
        promos=db.list_promos(),
        agents=db.top_agents(),
    )


@app.post("/pricing")
def pricing():
    if not auth_ok(request):
        return "Forbidden", 403
    db.set_setting("price_per_gb", request.form["price_per_gb"])
    db.set_setting("price_per_day", request.form["price_per_day"])
    return redirect(url_for("index", token=request.form["token"]))


@app.post("/promo")
def promo():
    if not auth_ok(request):
        return "Forbidden", 403
    max_uses = request.form.get("max_uses", "").strip()
    db.create_promo(
        code=request.form["code"],
        discount_percent=float(request.form["discount_percent"]),
        max_uses=int(max_uses) if max_uses else None,
    )
    return redirect(url_for("index", token=request.form["token"]))


if __name__ == "__main__":
    db.init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("ADMIN_WEB_PORT", "8080")))
