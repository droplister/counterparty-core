#
# file: counterpartycore/lib/api/migrations/0014.fix_assets_info_latest_issuance_columns.py
#
import logging
import time

from counterpartycore.lib import config
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

    # ATTACH the ledger DB so we can JOIN against issuances directly --
    # mirrors the pattern in 0006. Check first since prior migrations may
    # have already attached it (and DETACH while a write tx is open fails
    # with "database ledger_db is locked").
    attached = (
        db.execute(
            "SELECT COUNT(*) AS count FROM pragma_database_list WHERE name = ?", ("ledger_db",)
        ).fetchone()
    )
    attached_count = attached[0] if attached else 0
    if not attached_count:
        db.execute("ATTACH DATABASE ? AS ledger_db", (config.DATABASE,))

    db.execute(
        """
        UPDATE assets_info SET
            description = (
                SELECT i.description FROM ledger_db.issuances i
                WHERE i.asset = assets_info.asset AND i.status = 'valid'
                ORDER BY i.rowid DESC LIMIT 1
            ),
            divisible = (
                SELECT i.divisible FROM ledger_db.issuances i
                WHERE i.asset = assets_info.asset AND i.status = 'valid'
                ORDER BY i.rowid DESC LIMIT 1
            ),
            mime_type = (
                SELECT i.mime_type FROM ledger_db.issuances i
                WHERE i.asset = assets_info.asset AND i.status = 'valid'
                ORDER BY i.rowid DESC LIMIT 1
            ),
            owner = (
                SELECT i.issuer FROM ledger_db.issuances i
                WHERE i.asset = assets_info.asset AND i.status = 'valid'
                ORDER BY i.rowid DESC LIMIT 1
            )
        WHERE asset NOT IN ('XCP', 'BTC')
        """
    )

    # No DETACH: yoyo's transaction holds locks; explicit DETACH while
    # the write tx is open raises "database ledger_db is locked".
    # Following the 0006 pattern, leave ledger_db attached for subsequent
    # migrations.

    logger.debug("Re-derived assets_info rows in %.2f seconds", time.time() - start_time)


def rollback(db):
    # No-op: the migration is corrective, not schema-changing. Re-running
    # the prior buggy derivation would just restore the wrong values.
    pass


if not __name__.startswith("apsw_"):
    steps = [step(apply, rollback)]
