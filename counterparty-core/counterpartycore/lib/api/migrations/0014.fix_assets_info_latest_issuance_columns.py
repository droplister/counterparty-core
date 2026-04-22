#
# file: counterpartycore/lib/api/migrations/0014.fix_assets_info_latest_issuance_columns.py
#
import logging
import time

from counterpartycore.lib import config, database
from yoyo import step

logger = logging.getLogger(config.LOGGER_NAME)

__depends__ = {"0013.add_performance_indexes"}


def apply(db):
    """Re-derive description / divisible / mime_type / owner from the LATEST
    valid issuance per asset.

    Migration 0004 populated these columns via bare-column SELECT alongside
    multiple MIN/MAX aggregates -- per SQLite docs that picks bare columns
    "from one of" the min/max rows, implementation-dependent. Snapshot-
    bootstrapped nodes ended up with arbitrary (often first-issuance) values
    while event-streamed nodes (apiwatcher.update_assets_info) wrote latest-
    wins. API consumers got different answers depending on bootstrap mode.

    Re-derive deterministically from the latest valid issuance per asset.
    XCP is excluded (its row is hand-populated in 0004 and has no issuances
    in the issuances table to derive from).
    """
    start_time = time.time()
    logger.debug("Re-deriving assets_info latest-issuance columns...")

    ledger_db = database.get_db_connection(config.DATABASE)
    cursor = ledger_db.cursor()

    # Per-asset latest-valid-issuance row, keyed by MAX(rowid).
    cursor.execute(
        """
        SELECT i.asset, i.description, i.divisible, i.mime_type, i.issuer AS owner
        FROM issuances i
        JOIN (
            SELECT asset, MAX(rowid) AS max_rowid
            FROM issuances
            WHERE status = 'valid'
            GROUP BY asset
        ) latest ON latest.max_rowid = i.rowid
        """
    )
    rows = cursor.fetchall()
    cursor.close()

    update_cursor = db.cursor()
    update_cursor.execute("BEGIN")
    try:
        for row in rows:
            update_cursor.execute(
                """
                UPDATE assets_info
                SET description = ?, divisible = ?, mime_type = ?, owner = ?
                WHERE asset = ?
                """,
                (row[1], row[2], row[3], row[4], row[0]),
            )
        update_cursor.execute("COMMIT")
    except Exception:
        update_cursor.execute("ROLLBACK")
        raise
    finally:
        update_cursor.close()

    logger.debug(
        "Re-derived %d assets_info rows in %.2f seconds", len(rows), time.time() - start_time
    )


def rollback(db):
    # No-op: the migration is corrective, not schema-changing. Re-running
    # the prior buggy derivation would just restore the wrong values.
    pass


if not __name__.startswith("apsw_"):
    steps = [step(apply, rollback)]
