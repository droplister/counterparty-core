#
# file: counterpartycore/lib/api/migrations/0015.fix_assets_info_locked_int_drift.py
#
import logging
import time

from counterpartycore.lib import config
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

    # 0014 attaches ledger_db (and doesn't DETACH per the 0006 pattern);
    # attach defensively if not present (e.g. 0015 re-applied alone).
    attached = db.execute(
        "SELECT COUNT(*) AS count FROM pragma_database_list WHERE name = ?", ("ledger_db",)
    ).fetchone()
    attached_count = attached[0] if attached else 0
    if not attached_count:
        db.execute("ATTACH DATABASE ? AS ledger_db", (config.DATABASE,))

    db.execute(
        """
        UPDATE assets_info SET
            locked = COALESCE(
                (SELECT MAX(i.locked) FROM ledger_db.issuances i
                 WHERE i.asset = assets_info.asset AND i.status = 'valid'),
                locked
            ),
            description_locked = COALESCE(
                (SELECT MAX(i.description_locked) FROM ledger_db.issuances i
                 WHERE i.asset = assets_info.asset AND i.status = 'valid'),
                description_locked
            )
        WHERE asset NOT IN ('XCP', 'BTC')
        """
    )

    logger.debug(
        "Re-derived assets_info.locked + description_locked in %.2f seconds",
        time.time() - start_time,
    )


def rollback(db):
    pass


if not __name__.startswith("apsw_"):
    steps = [step(apply, rollback)]
