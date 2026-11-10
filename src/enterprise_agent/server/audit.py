"""Request audit storage and user-scoped history queries."""

import hashlib

from enterprise_agent.server.auth import _now, app_db


def audit(
    user_id,
    action,
    status,
    question=None,
    sql_hash=None,
    snapshot_id=None,
    metric_version=None,
    duration_ms=None,
    detail=None,
):
    with app_db() as db:
        cur = db.execute(
            "INSERT INTO audit(at,user_id,action,status,question_hash,sql_hash,snapshot_id,metric_version,duration_ms,detail) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                _now().isoformat(),
                user_id,
                action,
                status,
                hashlib.sha256(question.encode()).hexdigest() if question else None,
                sql_hash,
                snapshot_id,
                metric_version,
                duration_ms,
                detail[:500] if detail else None,
            ),
        )
        return cur.fetchone()["id"]


def list_audit(user, limit=50):
    with app_db() as db:
        if user["role"] == "admin":
            rows = db.execute(
                "SELECT a.id,a.at,u.username,a.action,a.status,a.sql_hash,a.snapshot_id,a.metric_version,a.duration_ms,a.detail FROM audit a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id,at,action,status,sql_hash,snapshot_id,metric_version,duration_ms,detail FROM audit WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                (user["id"], limit),
            ).fetchall()
    return [dict(r) for r in rows]
