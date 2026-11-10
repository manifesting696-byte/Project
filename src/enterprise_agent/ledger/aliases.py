"""Read-only aliases for historical Task URLs; SQL names are frozen archive contracts.

This is live compatibility code, not the offline SQLite implementation in ops/.
"""

ALIAS_TABLES = [
    "CREATE TABLE IF NOT EXISTS legacy_tasks(id TEXT PRIMARY KEY,body JSONB NOT NULL)",
    "CREATE TABLE IF NOT EXISTS legacy_task_revisions(id TEXT PRIMARY KEY,body JSONB NOT NULL)",
]


def archived_request_ids(self, task_id):
    with self.db() as db:
        row = db.execute(
            "SELECT body FROM legacy_tasks WHERE id=%s", (task_id,)
        ).fetchone()
        if row is None:
            return []
        task = row[0]
        row = db.execute(
            "SELECT body FROM legacy_task_revisions WHERE id=%s",
            (task["current_revision_id"],),
        ).fetchone()
    return [item["request_id"] for item in row[0]["requirements"]] if row else []
