import csv
import io
from datetime import datetime, timezone
from typing import Iterable, Sequence

from flask import send_file

import db
from admin.services import format_ts


def csv_file_response(filename: str, headers: Sequence[str], rows: Iterable[Sequence]):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name=filename)


def export_transactions_csv():
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
                format_ts(int(row["created_at"])),
            ]

    headers = ["id", "user_id", "username", "amount", "type", "description", "created_at"]
    return csv_file_response("transactions.csv", headers, rows())


def export_clients_csv():
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
                format_ts(int(row["created_at"])),
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
    return csv_file_response("clients.csv", headers, rows())


def export_agents_csv():
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
    return csv_file_response("agents.csv", headers, rows())


def build_bulk_promos_csv(codes, discount_type, value, max_uses_value, expires_at):
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
