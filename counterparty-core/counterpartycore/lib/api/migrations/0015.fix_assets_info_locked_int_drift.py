#
# file: counterpartycore/lib/api/migrations/0015.fix_assets_info_locked_int_drift.py
#
import logging
import time

from counterpartycore.lib import config
from counterpartycore.lib.utils import database
from yoyo import step

logger = logging.getLogger(config.LOGGER_NAME)

__depends__ = {"0014.fix_assets_info_latest_issuance_columns"}


def apply(db):
    """Re-derive assets_info.locked AND description_locked as 0/1 booleans.

    Migration 0004 populated both via `SUM(...)` over all valid issuances
    per asset, yielding integer counts (e.g. 2, 3) into columns declared
    `BOOL DEFAULT 0`. The streamed apiwatcher.update_assets_info writes
    each as `:locked` / `:description_locked` (boolean 0/1) latest-wins.
    Snapshot-bootstrapped nodes returned `"locked": 3` /
    `"description_locked": 3` for assets with three locking issuances;
    event-streamed nodes returned `1`. Both truthy but unequal -- clients
    comparing `=== 1` or summing across nodes diverged.

    Re-derive deterministically as `MAX(...) ∈ {0, 1}` matching the
    streamed writer's semantics. XCP is excluded (its row is hand-populated
    in 0004 and has no issuances to derive from).
    """
    start_time = time.time()
    logger.debug("Re-deriving assets_info.locked + description_locked as booleans...")

    ledger_db = database.get_db_connection(config.DATABASE)
    cursor = ledger_db.cursor()

    cursor.execute(
        """
        SELECT asset, MAX(locked) AS locked, MAX(description_locked) AS description_locked
        FROM issuances
        WHERE status = 'valid'
        GROUP BY asset
        """
    )
    rows = cursor.fetchall()
    cursor.close()

    update_cursor = db.cursor()
    update_cursor.execute("BEGIN")
    try:
        for row in rows:
            update_cursor.execute(
                "UPDATE assets_info SET locked = ?, description_locked = ? WHERE asset = ?",
                (row[1], row[2], row[0]),
            )
        update_cursor.execute("COMMIT")
    except Exception:
        update_cursor.execute("ROLLBACK")
        raise
    finally:
        update_cursor.close()

    logger.debug(
        "Re-derived assets_info.locked + description_locked for %d rows in %.2f seconds",
        len(rows),
        time.time() - start_time,
    )


def rollback(db):
    pass


if not __name__.startswith("apsw_"):
    steps = [step(apply, rollback)]
