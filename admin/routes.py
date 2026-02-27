import time

from flask import flash, redirect, render_template, request, url_for

import db
from admin.auth import auth_ok
from admin.exports import build_bulk_promos_csv, export_agents_csv, export_clients_csv, export_transactions_csv
from admin.services import BASE_STYLE, format_ts, generate_promo_code, get_user_detail_payload, list_recent_referrals, list_users_formatted, run_broadcast, run_notify_topup_result


def register_routes(app):
    @app.get("/")
    @app.get("/admin")
    @app.get("/admin/")
    def index():
        if not auth_ok(request):
            return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
        return render_template(
            "admin_web_panel/index.html",
            token=request.args.get("token"),
            price_per_gb=db.get_setting_float("price_per_gb"),
            price_per_day=db.get_setting_float("price_per_day"),
            price_unlimited_ip1=db.get_setting_float("price_unlimited_ip1"),
            price_unlimited_ip2=db.get_setting_float("price_unlimited_ip2"),
            price_unlimited_ip3=db.get_setting_float("price_unlimited_ip3"),
            manual_payment_details=db.get_setting_text("manual_payment_details"),
            promos=db.list_promos(),
            agents=db.top_agents(),
            referral_stats=db.list_referral_stats(),
            referrals=list_recent_referrals(limit=50),
        )

    @app.get("/dashboard")
    def dashboard():
        if not auth_ok(request):
            return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
        return render_template(
            "admin_web_panel/dashboard.html",
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
        return render_template(
            "admin_web_panel/users.html",
            token=request.args.get("token"),
            search=search,
            users=list_users_formatted(search),
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
        codes = []
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
                return render_template(
                    "admin_web/bulk_promos.html",
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
                code = generate_promo_code(prefix)
                if code in generated:
                    continue
                if db.promo_code_exists(code):
                    continue
                generated.add(code)

            if len(generated) < quantity:
                flash("Unable to generate enough unique codes. Please try again.", "error")
                return render_template(
                    "admin_web/bulk_promos.html",
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
                return build_bulk_promos_csv(codes, discount_type, value, max_uses_value, expires_at)

        return render_template(
            "admin_web/bulk_promos.html",
            style=BASE_STYLE,
            token=request.args.get("token") or request.form.get("token"),
            form=form,
            codes=codes,
        )

    @app.get("/admin/user/<int:tg_id>")
    def user_detail(tg_id: int):
        if not auth_ok(request):
            return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
        user_row, transactions = get_user_detail_payload(tg_id)
        if not user_row:
            flash("User not found.", "error")
            return redirect(url_for("admin_users", token=request.args.get("token")))
        return render_template(
            "admin_web/user_detail.html",
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

    @app.post("/admin/user/<int:tg_id>/pricing")
    def set_user_pricing(tg_id: int):
        if not auth_ok(request):
            return "Forbidden", 403
        pgb = request.form.get("price_per_gb", "").strip()
        pday = request.form.get("price_per_day", "").strip()
        db.set_agent_registration(tg_id, True)
        db.set_agent_pricing(tg_id, float(pgb) if pgb else None, float(pday) if pday else None)
        flash("Agent pricing updated.", "success")
        return redirect(url_for("admin_users", token=request.form.get("token")))

    @app.get("/admin/topups")
    def admin_topups():
        if not auth_ok(request):
            return "Forbidden", 403
        return render_template("admin_web_panel/topups.html", token=request.args.get("token"), rows=db.list_topup_requests(limit=200))

    @app.get("/admin/topups/<int:topup_id>/confirm")
    def confirm_topup(topup_id: int):
        if not auth_ok(request):
            return "Forbidden", 403
        try:
            bal = db.approve_topup_request(topup_id, 0, "approved by admin panel")
            req = db.get_topup_request(topup_id)
            if req:
                run_notify_topup_result(int(req["tg_id"]), topup_id, bal)
            flash(f"Topup #{topup_id} confirmed", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_topups", token=request.args.get("token")))

    @app.route("/admin/plans", methods=["GET", "POST"])
    def admin_plans():
        if not auth_ok(request):
            return "Forbidden", 403
        if request.method == "POST":
            role_scope = (request.form.get("role_scope", "reseller").strip().lower() or "reseller")
            if role_scope not in {"reseller", "agent", "all"}:
                role_scope = "reseller"
            db.create_plan_template(
                request.form.get("title", "").strip() or "Plan",
                int(request.form.get("days", "30")),
                int(request.form.get("gb", "30")),
                int(request.form.get("limit_ip", "1")),
                role_scope,
            )
            flash("Plan created.", "success")
            return redirect(url_for("admin_plans", token=request.form.get("token")))
        return render_template("admin_web_panel/plans.html", token=request.args.get("token"), rows=db.list_plan_templates(None))

    @app.get("/admin/export/transactions.csv")
    def export_transactions():
        if not auth_ok(request):
            return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
        return export_transactions_csv()

    @app.get("/admin/export/clients.csv")
    def export_clients():
        if not auth_ok(request):
            return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
        return export_clients_csv()

    @app.get("/admin/export/agents.csv")
    def export_agents():
        if not auth_ok(request):
            return "Forbidden. Provide ?token=ADMIN_WEB_TOKEN", 403
        return export_agents_csv()

    @app.get("/broadcast")
    def broadcast_form():
        if not auth_ok(request):
            return "Forbidden", 403
        return render_template("admin_web_panel/broadcast.html", token=request.args.get("token"), result=None, message="")

    @app.post("/broadcast")
    def broadcast_send():
        if not auth_ok(request):
            return "Forbidden", 403
        message = request.form.get("message", "").strip()
        if not message:
            return render_template("admin_web_panel/broadcast.html", token=request.form.get("token"), result=None, message="")
        user_ids = db.get_all_user_ids()
        result = run_broadcast(message, user_ids)
        return render_template("admin_web_panel/broadcast.html", token=request.form.get("token"), result=result, message=message)

    @app.post("/pricing")
    def pricing():
        if not auth_ok(request):
            return "Forbidden", 403

        fields = {
            "price_per_gb": request.form.get("price_per_gb", "").strip(),
            "price_per_day": request.form.get("price_per_day", "").strip(),
            "price_unlimited_ip1": request.form.get("price_unlimited_ip1", "150000").strip(),
            "price_unlimited_ip2": request.form.get("price_unlimited_ip2", "230000").strip(),
            "price_unlimited_ip3": request.form.get("price_unlimited_ip3", "300000").strip(),
        }

        try:
            parsed = {k: float(v) for k, v in fields.items()}
        except ValueError:
            flash("Pricing values must be numeric.", "error")
            return redirect(url_for("index", token=request.form["token"]))

        if any(v < 0 for v in parsed.values()):
            flash("Pricing values must be zero or positive.", "error")
            return redirect(url_for("index", token=request.form["token"]))

        for key, value in parsed.items():
            db.set_setting(key, str(value))
        db.set_setting("manual_payment_details", request.form.get("manual_payment_details", "").strip())
        flash("Pricing settings updated.", "success")
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
