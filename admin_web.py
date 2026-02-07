import asyncio
import csv
import io
import os
import secrets
import string
import tempfile
import time
from datetime import datetime, timezone
from typing import Iterable, List, Sequence

from flask import Flask, flash, redirect, render_template_string, request, send_file, url_for
from telegram import Bot

import db

app = Flask(__name__)
app.secret_key = os.getenv("ADMIN_WEB_SECRET", "dev-secret")


def auth_ok(req) -> bool:
    expected = os.getenv("ADMIN_WEB_TOKEN", "")
    provided = req.args.get("token") or req.form.get("token")
    return bool(expected) and provided == expected


def _format_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _csv_file_response(filename: str, headers: Sequence[str], rows: Iterable[Sequence]):
    temp = tempfile.SpooledTemporaryFile(max_size=1_000_000, mode="w+b")
    wrapper = io.TextIOWrapper(temp, encoding="utf-8", newline="")
    writer = csv.writer(wrapper)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    wrapper.flush()
    temp.seek(0)
    return send_file(temp, mimetype="text/csv", as_attachment=True, download_name=filename)


def _generate_promo_code(prefix: str, length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    random_part = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"{prefix}{random_part}".upper()


BASE_STYLE = """
<style>
body{font-family:Arial;max-width:1100px;margin:20px auto;padding:0 12px}
.card{border:1px solid #ddd;border-radius:8px;padding:12px;margin-bottom:12px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;font-size:13px}
input,textarea{padding:6px;margin:4px;width:100%}
.flash{border-radius:6px;padding:8px;margin-bottom:10px}
.flash.success{background:#e7f6ea;border:1px solid #b8e0c2}
.flash.error{background:#fde8e8;border:1px solid #f5c2c7}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.pill{background:#f3f3f3;border-radius:6px;padding:8px;text-align:center}
a{color:#0b5ed7}
</style>
"""


INDEX = """
<!doctype html>
<title>Bot Admin</title>
{{style}}
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    <div class='card'>
      {% for category, message in messages %}
        <div class='flash {{category}}'>{{message}}</div>
      {% endfor %}
    </div>
  {% endif %}
{% endwith %}
<h1>Bot Admin Panel</h1>
<div class='card'>
  <h3>Quick links</h3>
  <a href='/dashboard?token={{token}}'>Dashboard</a> |
  <a href='/admin/users?token={{token}}'>Users/Agents</a> |
  <a href='/admin/promos/bulk-generate?token={{token}}'>Bulk Promos</a> |
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
<div class='card'>
  <h3>Referral performance</h3>
  <table><tr><th>TG ID</th><th>User</th><th>Role</th><th>Referred</th><th>Commission</th></tr>
  {% for r in referral_stats %}<tr><td>{{r['tg_id']}}</td><td>{{r['username']}}</td><td>{{r['role']}}</td><td>{{r['referred_count']}}</td><td>{{r['commission_total']}}</td></tr>{% endfor %}
  </table>
</div>
<div class='card'>
  <h3>Recent referrals</h3>
  <table><tr><th>User TG</th><th>User</th><th>Referrer TG</th><th>Referrer</th><th>Referred At</th></tr>
  {% for r in referrals %}<tr><td>{{r['user_tg_id']}}</td><td>{{r['user_username']}}</td><td>{{r['referrer_tg_id']}}</td><td>{{r['referrer_username']}}</td><td>{{r['referred_at']}}</td></tr>{% endfor %}
  </table>
</div>
"""


DASHBOARD = """
<!doctype html>
<title>Admin Dashboard</title>
{{style}}
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    <div class='card'>
      {% for category, message in messages %}
        <div class='flash {{category}}'>{{message}}</div>
      {% endfor %}
    </div>
  {% endif %}
{% endwith %}
<h1>Analytics Dashboard</h1>
<div class='card'>
  <a href='/?token={{token}}'>Back</a> |
  <a href='/broadcast?token={{token}}'>Broadcast</a> |
  <a href='/admin/users?token={{token}}'>Users/Agents</a>
</div>
<div class='card'>
  <h3>Exports</h3>
  <a href='/admin/export/transactions.csv?token={{token}}'>Transactions CSV</a> |
  <a href='/admin/export/clients.csv?token={{token}}'>Clients CSV</a> |
  <a href='/admin/export/agents.csv?token={{token}}'>Agents CSV</a>
</div>
<div class='card'>
  <h3>Promotions</h3>
  <a href='/admin/promos/bulk-generate?token={{token}}'>Bulk promo generator</a>
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


BROWSE_USERS = """
<!doctype html>
<title>Users/Agents</title>
{{style}}
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    <div class='card'>
      {% for category, message in messages %}
        <div class='flash {{category}}'>{{message}}</div>
      {% endfor %}
    </div>
  {% endif %}
{% endwith %}
<h1>User/Agent Search</h1>
<div class='card'>
  <a href='/dashboard?token={{token}}'>Back</a> |
  <a href='/admin/export/agents.csv?token={{token}}'>Export Agents CSV</a>
</div>
<div class='card'>
  <form method='get' action='/admin/users'>
    <input type='hidden' name='token' value='{{token}}'>
    Search by username, telegram ID, or role:
    <input name='search' value='{{search}}' placeholder='e.g. reseller or 12345 or @name'>
    <button>Search</button>
  </form>
</div>
<div class='card'>
  <table>
    <tr>
      <th>Username</th>
      <th>Telegram ID</th>
      <th>Role</th>
      <th>Balance</th>
      <th>Join Date</th>
      <th>Clients</th>
      <th>Action</th>
    </tr>
    {% for u in users %}
      <tr>
        <td>{{u['username'] or '-'}}</td>
        <td>{{u['tg_id']}}</td>
        <td>{{u['role']}}</td>
        <td>{{'%.2f'|format(u['balance'])}}</td>
        <td>{{u['created_at']}}</td>
        <td>{{u['client_count']}}</td>
        <td><a href='/admin/user/{{u["tg_id"]}}?token={{token}}'>View</a></td>
      </tr>
    {% endfor %}
  </table>
</div>
"""


USER_DETAIL = """
<!doctype html>
<title>User Detail</title>
{{style}}
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    <div class='card'>
      {% for category, message in messages %}
        <div class='flash {{category}}'>{{message}}</div>
      {% endfor %}
    </div>
  {% endif %}
{% endwith %}
<h1>User Detail</h1>
<div class='card'>
  <a href='/admin/users?token={{token}}'>Back</a>
</div>
<div class='card'>
  <h3>{{user['username'] or 'Unknown'}} ({{user['tg_id']}})</h3>
  <div>Role: <b>{{user['role']}}</b></div>
  <div>Balance: <b>{{'%.2f'|format(user['balance'])}}</b></div>
  <div>Joined: <b>{{user['created_at']}}</b></div>
  <div>Clients: <b>{{user['client_count']}}</b></div>
</div>
<div class='card'>
  <h3>Manual Balance Adjust</h3>
  <form method='post' action='/admin/user/{{user["tg_id"]}}/adjust'>
    <input type='hidden' name='token' value='{{token}}'>
    Amount (positive add / negative deduct):
    <input name='amount' placeholder='e.g. 10 or -5'>
    Reason:
    <input name='reason' placeholder='Required'>
    <button>Submit</button>
  </form>
</div>
<div class='card'>
  <h3>Recent Transactions</h3>
  <table>
    <tr><th>ID</th><th>Amount</th><th>Type</th><th>Description</th><th>Created</th></tr>
    {% for t in transactions %}
      <tr>
        <td>{{t['id']}}</td>
        <td>{{t['amount']}}</td>
        <td>{{t['reason']}}</td>
        <td>{{t['meta']}}</td>
        <td>{{t['created_at']}}</td>
      </tr>
    {% endfor %}
  </table>
</div>
"""


BULK_PROMOS = """
<!doctype html>
<title>Bulk Promo Generator</title>
{{style}}
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    <div class='card'>
      {% for category, message in messages %}
        <div class='flash {{category}}'>{{message}}</div>
      {% endfor %}
    </div>
  {% endif %}
{% endwith %}
<h1>Bulk Promo Code Generator</h1>
<div class='card'>
  <a href='/dashboard?token={{token}}'>Back</a>
</div>
<div class='card'>
  <form method='post' action='/admin/promos/bulk-generate'>
    <input type='hidden' name='token' value='{{token}}'>
    Quantity (1-1000): <input name='quantity' value='{{form.quantity}}'>
    Discount type:
    <select name='discount_type'>
      <option value='percent' {% if form.discount_type == 'percent' %}selected{% endif %}>Percentage</option>
      <option value='gb_free' {% if form.discount_type == 'gb_free' %}selected{% endif %}>Free GB</option>
      <option value='fixed_amount' {% if form.discount_type == 'fixed_amount' %}selected{% endif %}>Fixed amount</option>
    </select>
    Value: <input name='value' value='{{form.value}}'>
    Usage limit per code (0 = unlimited): <input name='max_uses' value='{{form.max_uses}}'>
    Expiry days (blank = no expiry): <input name='expiry_days' value='{{form.expiry_days}}'>
    Optional prefix: <input name='prefix' value='{{form.prefix}}' placeholder='SPRING25-'>
    <button name='action' value='generate'>Generate</button>
    <button name='action' value='download'>Generate & Download CSV</button>
  </form>
</div>
{% if codes %}
<div class='card'>
  <h3>Generated {{codes|length}} codes</h3>
  <textarea rows='10' readonly>{% for c in codes %}{{c}}\n{% endfor %}</textarea>
</div>
{% endif %}
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
    referrals = []
    for row in db.list_referrals(limit=50):
        item = dict(row)
        item["referred_at"] = _format_ts(int(item["referred_at"]))
        referrals.append(item)
    return render_template_string(
        INDEX,
        style=BASE_STYLE,
        token=request.args.get("token"),
        price_per_gb=db.get_setting_float("price_per_gb"),
        price_per_day=db.get_setting_float("price_per_day"),
        promos=db.list_promos(),
        agents=db.top_agents(),
        referral_stats=db.list_referral_stats(),
        referrals=referrals,
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


@app.get("/admin/users")
def admin_users():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    search = request.args.get("search", "").strip()
    if search:
        users = db.search_agents(search)
    else:
        users = db.list_agents()
    formatted = []
    for u in users:
        row = dict(u)
        row["created_at"] = _format_ts(int(row["created_at"]))
        formatted.append(row)
    return render_template_string(
        BROWSE_USERS,
        style=BASE_STYLE,
        token=request.args.get("token"),
        search=search,
        users=formatted,
    )


@app.route("/admin/promos/bulk-generate", methods=["GET", "POST"])
def bulk_generate_promos():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    form = {
        "quantity": request.form.get("quantity", "50"),
        "discount_type": request.form.get("discount_type", "percent"),
        "value": request.form.get("value", ""),
        "max_uses": request.form.get("max_uses", "1"),
        "expiry_days": request.form.get("expiry_days", ""),
        "prefix": request.form.get("prefix", ""),
    }
    codes: List[str] = []
    if request.method == "POST":
        try:
            quantity = int(form["quantity"])
            if quantity < 1 or quantity > 1000:
                raise ValueError("Quantity must be between 1 and 1000.")
            value = float(form["value"])
            if value <= 0:
                raise ValueError("Value must be greater than 0.")
            max_uses = int(form["max_uses"])
            if max_uses < 0:
                raise ValueError("Usage limit must be 0 or greater.")
            expiry_days = form["expiry_days"].strip()
            expires_at = None
            if expiry_days:
                days_val = int(expiry_days)
                if days_val <= 0:
                    raise ValueError("Expiry days must be greater than 0.")
                expires_at = int(time.time() + days_val * 86400)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template_string(
                BULK_PROMOS,
                style=BASE_STYLE,
                token=request.args.get("token") or request.form.get("token"),
                form=form,
                codes=codes,
            )

        prefix = form["prefix"].strip().upper()
        generated = set()
        attempts = 0
        while len(generated) < quantity and attempts < quantity * 20:
            attempts += 1
            code = _generate_promo_code(prefix)
            if code in generated:
                continue
            if db.promo_code_exists(code):
                continue
            generated.add(code)

        if len(generated) < quantity:
            flash("Unable to generate enough unique codes. Please try again.", "error")
            return render_template_string(
                BULK_PROMOS,
                style=BASE_STYLE,
                token=request.args.get("token") or request.form.get("token"),
                form=form,
                codes=sorted(generated),
            )

        discount_type = form["discount_type"]
        discount_percent = value if discount_type == "percent" else 0
        max_uses_value = None if max_uses == 0 else max_uses
        created_at = int(time.time())
        created_by = None

        rows = []
        for code in generated:
            rows.append(
                {
                    "code": code,
                    "discount_percent": discount_percent,
                    "discount_type": discount_type,
                    "value": value,
                    "max_uses": max_uses_value,
                    "used_count": 0,
                    "active": 1,
                    "expires_at": expires_at,
                    "created_at": created_at,
                    "created_by": created_by,
                }
            )
        db.insert_promo_batch(rows)
        codes = sorted(generated)
        flash(f"Generated {len(codes)} codes.", "success")

        if request.form.get("action") == "download":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["code", "discount_type", "value", "max_uses", "expires_at"])
            for code in codes:
                writer.writerow([code, discount_type, value, max_uses_value, expires_at])
            output.seek(0)
            return send_file(
                io.BytesIO(output.getvalue().encode("utf-8")),
                mimetype="text/csv",
                as_attachment=True,
                download_name="bulk_promos.csv",
            )

    return render_template_string(
        BULK_PROMOS,
        style=BASE_STYLE,
        token=request.args.get("token") or request.form.get("token"),
        form=form,
        codes=codes,
    )


@app.get("/admin/user/<int:tg_id>")
def user_detail(tg_id: int):
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    user = db.get_agent_with_client_count(tg_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin_users", token=request.args.get("token")))
    user_row = dict(user)
    user_row["created_at"] = _format_ts(int(user_row["created_at"]))
    transactions = []
    for t in db.list_transactions(tg_id, limit=20):
        row = dict(t)
        row["created_at"] = _format_ts(int(row["created_at"]))
        transactions.append(row)
    return render_template_string(
        USER_DETAIL,
        style=BASE_STYLE,
        token=request.args.get("token"),
        user=user_row,
        transactions=transactions,
    )


@app.post("/admin/user/<int:tg_id>/adjust")
def manual_adjust(tg_id: int):
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
    amount_text = request.form.get("amount", "").strip()
    reason = request.form.get("reason", "").strip()
    if not amount_text or not reason:
        flash("Amount and reason are required.", "error")
        return redirect(url_for("user_detail", tg_id=tg_id, token=request.form.get("token")))
    try:
        amount = float(amount_text)
    except ValueError:
        flash("Amount must be a number.", "error")
        return redirect(url_for("user_detail", tg_id=tg_id, token=request.form.get("token")))
    note = f"Admin manual: {reason}"
    db.manual_adjust_balance(tg_id, amount, "manual_adjust", note)
    flash("Balance updated successfully.", "success")
    return redirect(url_for("user_detail", tg_id=tg_id, token=request.form.get("token")))


@app.get("/admin/export/transactions.csv")
def export_transactions():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403

    def rows():
        for row in db.iter_transactions_export():
            description = row["meta"] or ""
            yield [
                row["id"],
                row["tg_id"],
                row["username"] or "",
                row["amount"],
                row["reason"],
                description,
                _format_ts(int(row["created_at"])),
            ]

    headers = ["id", "user_id", "username", "amount", "type", "description", "created_at"]
    return _csv_file_response("transactions.csv", headers, rows())


@app.get("/admin/export/clients.csv")
def export_clients():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403

    def rows():
        now = datetime.now(tz=timezone.utc)
        for row in db.iter_clients_export():
            created_at = datetime.fromtimestamp(int(row["created_at"]), tz=timezone.utc)
            days_used = max((now - created_at).days, 0)
            days_left = max(int(row["days"]) - days_used, 0)
            status = "active" if days_left > 0 else "expired"
            yield [
                row["tg_id"],
                row["username"] or "",
                row["email"],
                row["uuid"],
                row["gb"],
                0,
                days_left,
                status,
                _format_ts(int(row["created_at"])),
            ]

    headers = [
        "user_id",
        "username",
        "remark",
        "uuid",
        "total_gb",
        "used_gb",
        "days_left",
        "status",
        "created_at",
    ]
    return _csv_file_response("clients.csv", headers, rows())


@app.get("/admin/export/agents.csv")
def export_agents():
    if not auth_ok(request):
        return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403

    def rows():
        for row in db.iter_agents_export():
            yield [
                row["username"] or "",
                row["tg_id"],
                row["balance"],
                row["client_count"],
                row["total_revenue"],
            ]

    headers = ["username", "telegram_id", "balance", "client_count", "total_revenue"]
    return _csv_file_response("agents.csv", headers, rows())


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
