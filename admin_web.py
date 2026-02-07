import asyncio
import os
from typing import List

from flask import Flask, redirect, render_template_string, request, url_for
from telegram import Bot

import db

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_WEB_SECRET", "dev-secret")


def auth_ok(req) -> bool:
    expected = os.getenv("ADMIN_WEB_TOKEN", "")
    provided = req.args.get("token") or req.form.get("token")
    return bool(expected) and provided == expected


BASE_STYLE = """
<style>
body{font-family:Arial;max-width:1100px;margin:20px auto;padding:0 12px}
.card{border:1px solid #ddd;border-radius:8px;padding:12px;margin-bottom:12px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;font-size:13px}
input,textarea{padding:6px;margin:4px;width:100%}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.pill{background:#f3f3f3;border-radius:6px;padding:8px;text-align:center}
a{color:#0b5ed7}
</style>
"""


INDEX = """
<!doctype html>
<title>Bot Admin</title>
{{style}}
<h1>Bot Admin Panel</h1>
<div class='card'>
  <h3>Quick links</h3>
  <a href='/dashboard?token={{token}}'>Dashboard</a> |
  <a href='/?token={{token}}'>Pricing/Promos</a> |
  <a href='/broadcast?token={{token}}'>Broadcast</a>
</div>
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


DASHBOARD = """
<!doctype html>
<title>Admin Dashboard</title>
{{style}}
<h1>Analytics Dashboard</h1>
<div class='card'>
  <a href='/?token={{token}}'>Back</a> | <a href='/broadcast?token={{token}}'>Broadcast</a>
</div>
<div class='card grid'>
  <div class='pill'>Resellers<br><b>{{resellers}}</b></div>
  <div class='pill'>Clients<br><b>{{clients}}</b></div>
  <div class='pill'>Revenue<br><b>{{revenue}}</b></div>
  <div class='pill'>Promo Codes<br><b>{{promos}}</b></div>
</div>
<div class='card'>
  <h3>Top 5 Resellers (Revenue)</h3>
  <table><tr><th>User</th><th>Revenue</th><th>Clients</th></tr>
  {% for r in top_rev %}<tr><td>{{r['username'] or r['tg_id']}}</td><td>{{r['revenue']}}</td><td>{{r['clients']}}</td></tr>{% endfor %}
  </table>
</div>
<div class='card'>
  <h3>Top 5 Resellers (Clients)</h3>
  <table><tr><th>User</th><th>Clients</th><th>Revenue</th></tr>
  {% for r in top_clients %}<tr><td>{{r['username'] or r['tg_id']}}</td><td>{{r['clients']}}</td><td>{{r['revenue']}}</td></tr>{% endfor %}
  </table>
</div>
<div class='card'>
  <h3>Daily Sales (last 14 days)</h3>
  <table><tr><th>Date</th><th>Revenue</th></tr>
  {% for d in daily %}<tr><td>{{d['day']}}</td><td>{{d['revenue']}}</td></tr>{% endfor %}
  </table>
</div>
<div class='card'>
  <h3>Monthly Sales (last 6 months)</h3>
  <table><tr><th>Month</th><th>Revenue</th></tr>
  {% for m in monthly %}<tr><td>{{m['month']}}</td><td>{{m['revenue']}}</td></tr>{% endfor %}
  </table>
</div>
"""


BROADCAST = """
<!doctype html>
<title>Broadcast</title>
{{style}}
<h1>Broadcast Message</h1>
<div class='card'>
  <a href='/?token={{token}}'>Back</a> | <a href='/dashboard?token={{token}}'>Dashboard</a>
</div>
<div class='card'>
  <form method='post' action='/broadcast'>
    <input type='hidden' name='token' value='{{token}}'>
    <textarea name='message' rows='6' placeholder='Your message...'></textarea>
    <button>Send Broadcast</button>
  </form>
</div>
{% if result %}
<div class='card'>
  <h3>Result</h3>
  <div>Sent: {{result.sent}}</div>
  <div>Failed: {{result.failed}}</div>
</div>
{% endif %}
"""


@app.get("/")
def index():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    return render_template_string(
        INDEX,
        style=BASE_STYLE,
        token=request.args.get("token"),
        price_per_gb=db.get_setting_float("price_per_gb"),
        price_per_day=db.get_setting_float("price_per_day"),
        promos=db.list_promos(),
        agents=db.top_agents(),
    )


@app.get("/dashboard")
def dashboard():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    return render_template_string(
        DASHBOARD,
        style=BASE_STYLE,
        token=request.args.get("token"),
        resellers=db.count_resellers(),
        clients=db.count_all_clients(),
        revenue=db.total_revenue(),
        promos=len(db.list_promos()),
        top_rev=db.top_resellers_by_revenue(),
        top_clients=db.top_resellers_by_clients(),
        daily=db.sales_by_day(14),
        monthly=db.sales_by_month(6),
    )


@app.get("/broadcast")
def broadcast_form():
    if not auth_ok(request):
        return "Forbidden", 403
    return render_template_string(BROADCAST, style=BASE_STYLE, token=request.args.get("token"), result=None)


async def _broadcast(message: str, user_ids: List[int]) -> dict:
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN", ""))
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=message)
            sent += 1
        except Exception:
            failed += 1
    return {"sent": sent, "failed": failed}


@app.post("/broadcast")
def broadcast_send():
    if not auth_ok(request):
        return "Forbidden", 403
    message = request.form.get("message", "").strip()
    if not message:
        return render_template_string(BROADCAST, style=BASE_STYLE, token=request.form.get("token"), result=None)
    user_ids = db.get_all_user_ids()
    result = asyncio.run(_broadcast(message, user_ids))
    return render_template_string(BROADCAST, style=BASE_STYLE, token=request.form.get("token"), result=result)


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
