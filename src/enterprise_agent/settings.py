"""Deployment paths and explicit resource limits, independent of business scenarios."""

import os
from pathlib import Path

# settings.py lives at <project>/src/enterprise_agent/settings.py, so parents[2] is <project>.
ROOT = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
WORK = Path(os.getenv("AGENT_WORK_DIR", str(ROOT / "runtime")))
MAX_TASKS = int(os.getenv("MAX_TASKS", os.getenv("MAX_SUBTASKS", "60")))
MAX_REPLANS = int(os.getenv("MAX_REPLANS", "8"))
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "12"))
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "4"))
READ_ROWS = int(os.getenv("ARTIFACT_READ_ROWS", "100"))
RECENT_EVENTS = int(os.getenv("CONTEXT_RECENT_EVENTS", "24"))
SUMMARY_THRESHOLD = int(os.getenv("SUMMARY_EVENT_THRESHOLD", "80"))


def setting(name, default=None):
    value = os.getenv(name)
    if value:
        return value
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, candidate = line.split("=", 1)
            if key.strip() == name:
                return candidate.strip().strip('"').strip("'") or default
    return default


def database_url(name="APP_DATABASE_URL"):
    value = setting(name)
    if not value:
        raise ValueError(f"请配置 {name}，并先启动 PostgreSQL")
    return value


def model_settings():
    values = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    values = {
        key: os.getenv(key) or values.get(key, "")
        for key in ("LLM_URL", "LLM_API_KEY", "LLM_MODEL")
    }
    if not all(values.values()):
        raise ValueError("请配置 LLM_URL、LLM_API_KEY、LLM_MODEL")
    base = values["LLM_URL"].rstrip("/")
    if not base.endswith("/chat/completions"):
        base += (
            "/chat/completions"
            if base.split("://", 1)[-1].find("/") >= 0
            else "/v1/chat/completions"
        )
    return base, values["LLM_API_KEY"], values["LLM_MODEL"]
