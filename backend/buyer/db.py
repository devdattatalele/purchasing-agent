import json
import os
from datetime import date, datetime
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()
DSN = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/buyer_agent")


def connect() -> psycopg.Connection:
    return psycopg.connect(DSN, row_factory=dict_row, autocommit=True)


def rows(sql: str, params=None) -> list[dict]:
    with connect() as c:
        return [clean(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params=None) -> dict | None:
    found = rows(sql, params)
    return found[0] if found else None


def run(sql: str, params=None) -> None:
    with connect() as c:
        c.execute(sql, params)


def clean(value):
    """Decimals and dates out of psycopg into plain JSON friendly values."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def dumps(value) -> str:
    return json.dumps(clean(value))


def today() -> date:
    return date.fromisoformat(one("SELECT today FROM world")["today"])


def log_step(run_id: int, kind: str, title: str, payload=None) -> None:
    run("INSERT INTO agent_steps (run_id, kind, title, payload) VALUES (%s, %s, %s, %s)",
        (run_id, kind, title, dumps(payload)))
