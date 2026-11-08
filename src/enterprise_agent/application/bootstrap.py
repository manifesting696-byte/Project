"""Initialize source snapshot, identity and the four stores without invoking a model."""
import hashlib
import json
from ..core.config import WORK
from ..profile.data import DB, profile, import_data
from ..api.identity import bootstrap_admin
from ..persistence.stores import Stores


def main():
    profiles, _ = profile()
    expected = hashlib.sha256("".join(item["sha256"] for item in profiles.values()).encode()).hexdigest()[:16]
    manifest = WORK / "manifest.json"
    current = json.loads(manifest.read_text(encoding="utf-8"))["snapshot_id"] if manifest.exists() else None
    if not DB.exists() or current != expected:
        import_data()
    bootstrap_admin()
    Stores()
    print("数据、账号和四类存储已就绪")


if __name__ == "__main__":
    main()
