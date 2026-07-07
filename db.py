"""SQLite job tracking for MinecraftCast.

Async access via aiosqlite. A single jobs table records the full lifecycle of
every video generation job so concurrent orders can be tracked independently.
"""

import json
import datetime
import aiosqlite

DB_PATH = "minecraftcast.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    input_json  TEXT,
    output_url  TEXT,
    progress    INTEGER DEFAULT 0,
    created_at  TEXT,
    updated_at  TEXT,
    error       TEXT
)
"""


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


async def init_db() -> None:
    """Create the jobs table if it does not exist. Call once at startup."""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(_CREATE_TABLE)
        await conn.commit()


async def create_job(job_id: str, input_data: dict) -> None:
    """Insert a new job row in the 'queued' state."""
    ts = _now()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO jobs
               (id, status, input_json, output_url, progress, created_at, updated_at, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                "queued",
                json.dumps(input_data),
                None,
                0,
                ts,
                ts,
                None,
            ),
        )
        await conn.commit()


async def update_job(
    job_id: str,
    status: str | None = None,
    progress: int | None = None,
    output_url: str | None = None,
    error: str | None = None,
) -> None:
    """Patch any subset of mutable job fields."""
    sets = ["updated_at = ?"]
    vals: list = [_now()]

    if status is not None:
        sets.append("status = ?")
        vals.append(status)
    if progress is not None:
        sets.append("progress = ?")
        vals.append(progress)
    if output_url is not None:
        sets.append("output_url = ?")
        vals.append(output_url)
    if error is not None:
        sets.append("error = ?")
        vals.append(error)

    vals.append(job_id)
    sql = f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?"

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(sql, vals)
        await conn.commit()


async def get_job(job_id: str) -> dict | None:
    """Return a job row as a dict, or None if not found."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return dict(row)
