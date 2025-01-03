import json
import logging
import os
import threading
import time
from random import randrange

import apsw
from counterpartycore.lib import blocks, config, database, exceptions, ledger
from counterpartycore.lib.api import util
from counterpartycore.lib.util import format_duration
from yoyo import get_backend, read_migrations
from yoyo.exceptions import LockTimeout

logger = logging.getLogger(config.LOGGER_NAME)

CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))
MIGRATIONS_DIR = os.path.join(CURRENT_DIR, "migrations")

UPDATE_EVENTS_ID_FIELDS = {
    "BLOCK_PARSED": ["block_index"],
    "TRANSACTION_PARSED": ["tx_hash"],
    "BET_MATCH_UPDATE": ["id"],
    "BET_UPDATE": ["tx_hash"],
    "DISPENSER_UPDATE": ["tx_hash"],
    "ORDER_FILLED": ["tx_hash"],
    "ORDER_MATCH_UPDATE": ["id"],
    "ORDER_UPDATE": ["tx_hash"],
    "RPS_MATCH_UPDATE": ["id"],
    "RPS_UPDATE": ["tx_hash"],
    "ADDRESS_OPTIONS_UPDATE": ["address"],
    "FAIRMINTER_UPDATE": ["tx_hash"],
}

EXPIRATION_EVENTS_OBJECT_ID = {
    "ORDER_EXPIRATION": "order_hash",
    "ORDER_MATCH_EXPIRATION": "order_match_id",
    "RPS_EXPIRATION": "rps_hash",
    "RPS_MATCH_EXPIRATION": "rps_match_id",
    "BET_EXPIRATION": "bet_hash",
    "BET_MATCH_EXPIRATION": "bet_match_id",
}

ASSET_EVENTS = [
    "ASSET_CREATION",
    "ASSET_ISSUANCE",
    "ASSET_DESTRUCTION",
    "RESET_ISSUANCE",
    "ASSET_TRANSFER",
    "BURN",
]

XCP_DESTROY_EVENTS = [
    "ASSET_ISSUANCE",
    "ASSET_DESTRUCTION",
    "SWEEP",
    "ASSET_DIVIDEND",
]

EVENTS_ADDRESS_FIELDS = {
    "NEW_TRANSACTION": ["source", "destination"],
    "DEBIT": ["address"],
    "CREDIT": ["address"],
    "ENHANCED_SEND": ["source", "destination"],
    "MPMA_SEND": ["source", "destination"],
    "SEND": ["source", "destination"],
    "ASSET_TRANSFER": ["source", "issuer"],
    "SWEEP": ["source", "destination"],
    "ASSET_DIVIDEND": ["source"],
    "RESET_ISSUANCE": ["source", "issuer"],
    "ASSET_ISSUANCE": ["source", "issuer"],
    "ASSET_DESTRUCTION": ["source"],
    "OPEN_ORDER": ["source"],
    "ORDER_MATCH": ["tx0_address", "tx1_address"],
    "BTC_PAY": ["source", "destination"],
    "CANCEL_ORDER": ["source"],
    "ORDER_EXPIRATION": ["source"],
    "ORDER_MATCH_EXPIRATION": ["tx0_address", "tx1_address"],
    "OPEN_DISPENSER": ["source", "origin", "oracle_address"],
    "DISPENSER_UPDATE": ["source"],
    "REFILL_DISPENSER": ["source", "destination"],
    "DISPENSE": ["source", "destination"],
    "BROADCAST": ["source"],
    "BURN": ["source"],
    "NEW_FAIRMINT": ["source"],
    "NEW_FAIRMINTER": ["source"],
}

SKIP_EVENTS = ["NEW_TRANSACTION_OUTPUT"]

MEMPOOL_SKIP_EVENT_HASHES = []


def fetch_all(db, query, bindings=None):
    cursor = db.cursor()
    cursor.execute(query, bindings)
    return cursor.fetchall()


def fetch_one(db, query, bindings=None):
    cursor = db.cursor()
    cursor.execute(query, bindings)
    return cursor.fetchone()


def delete_all(db, query, bindings=None):
    cursor = db.cursor()
    cursor.execute(query, bindings)
    changes = fetch_one(db, "SELECT changes() AS deleted")
    return changes["deleted"]


def get_last_parsed_message_index(api_db):
    sql = "SELECT * FROM messages ORDER BY message_index DESC LIMIT 1"
    last_event = fetch_one(api_db, sql)
    last_message_index = -1
    if last_event:
        last_message_index = last_event["message_index"]
    return last_message_index


def get_next_event_to_parse(api_db, ledger_db):
    last_parsed_message_index = get_last_parsed_message_index(api_db)
    sql = "SELECT * FROM messages WHERE message_index = ?"
    next_event = fetch_one(ledger_db, sql, (last_parsed_message_index + 1,))
    return next_event


def get_event_to_parse_count(api_db, ledger_db):
    last_parsed_message_index = get_last_parsed_message_index(api_db)
    sql = "SELECT message_index FROM messages ORDER BY message_index DESC LIMIT 1"
    last_event = fetch_one(ledger_db, sql)
    if last_event is None:
        return 0
    return last_event["message_index"] - last_parsed_message_index


def get_event_bindings(event):
    event_bindings = json.loads(event["bindings"])
    if (
        "order_match_id" in event_bindings
        and "id" in event_bindings
        and event_bindings["order_match_id"] == event_bindings["id"]
    ):
        del event_bindings["order_match_id"]
    return event_bindings


def insert_event_to_sql(event):
    event_bindings = get_event_bindings(event)
    event_bindings["block_index"] = event["block_index"]
    sql_bindings = []
    sql = f"INSERT INTO {event['category']} "
    names = []
    for key, value in event_bindings.items():
        names.append(key)
        sql_bindings.append(value)
    sql += f"({', '.join(names)}) VALUES ({', '.join(['?' for _ in names])})"
    return sql, sql_bindings


def update_event_to_sql(event):
    event_bindings = get_event_bindings(event)
    event_bindings["block_index"] = event["block_index"]

    if event_bindings["block_index"] == config.MEMPOOL_BLOCK_INDEX:
        return None, []

    id_field_names = UPDATE_EVENTS_ID_FIELDS[event["event"]]

    sql_bindings = []
    sets = []
    for key, value in event_bindings.items():
        if key in id_field_names:
            continue
        sets.append(f"{key} = ?")
        sql_bindings.append(value)
    sets_clause = ", ".join(sets)

    where = []
    where_bindings = []
    for id_field_name in id_field_names:
        where.append(f"{id_field_name} = ?")
        sql_bindings.append(event_bindings[id_field_name])
        where_bindings.append(event_bindings[id_field_name])
    where_clause = " AND ".join(where)

    sql = f"UPDATE {event['category']} SET {sets_clause} WHERE {where_clause}"  # noqa: S608

    return sql, sql_bindings


def event_to_sql(event):
    if event["command"] == "insert":
        return insert_event_to_sql(event)
    if event["command"] in ["update", "parse"]:
        return update_event_to_sql(event)
    return None, []


def get_event_previous_state(api_db, event):
    previous_state = None
    if event["command"] in ["update", "parse"]:
        id_field_names = UPDATE_EVENTS_ID_FIELDS[event["event"]]

        where = []
        for id_field_name in id_field_names:
            where.append(f"{id_field_name} = :{id_field_name}")
        where_clause = " AND ".join(where)

        sql = f"SELECT * FROM {event['category']} WHERE {where_clause}"  # noqa: S608
        event_bindings = json.loads(event["bindings"])
        previous_state = fetch_one(api_db, sql, event_bindings)
    return previous_state


def insert_event(api_db, event):
    previous_state = get_event_previous_state(api_db, event)
    if previous_state is not None:
        event["previous_state"] = util.to_json(previous_state)
    else:
        event["previous_state"] = None
    sql = """
        INSERT INTO messages 
            (message_index, block_index, event, category, command, bindings, tx_hash, previous_state, insert_rowid, event_hash)
        VALUES (:message_index, :block_index, :event, :category, :command, :bindings, :tx_hash, :previous_state, :insert_rowid, :event_hash)
    """
    cursor = api_db.cursor()
    cursor.execute(sql, event)


def rollback_event(api_db, event):
    logger.trace(f"API Watcher - Rolling back event: {event['message_index']} ({event['event']})")
    with api_db:  # all or
        if event["event"] in SKIP_EVENTS:
            sql = "DELETE FROM messages WHERE message_index = ?"
            delete_all(api_db, sql, (event["message_index"],))
            return
        if event["previous_state"] is None or event["previous_state"] == "null":
            sql = f"DELETE FROM {event['category']} WHERE rowid = ?"  # noqa: S608
            deleted = delete_all(api_db, sql, (event["insert_rowid"],))
            if deleted == 0:
                raise Exception(
                    f"Failed to delete event: {event['message_index']} ({event['event']})"
                )
        else:
            previous_state = json.loads(event["previous_state"])

            id_field_names = UPDATE_EVENTS_ID_FIELDS[event["event"]]

            sets = []
            for key in previous_state.keys():
                if key in id_field_names:
                    continue
                sets.append(f"{key} = :{key}")
            set_clause = ", ".join(sets)

            where = []
            for id_field_name in id_field_names:
                where.append(f"{id_field_name} = :{id_field_name}")
            where_clause = " AND ".join(where)

            sql = f"UPDATE {event['category']} SET {set_clause} WHERE {where_clause}"  # noqa: S608
            cursor = api_db.cursor()
            cursor.execute(sql, previous_state)

        rollback_balances(api_db, event)
        rollback_expiration(api_db, event)
        rollback_assets_info(api_db, event)
        rollback_xcp_supply(api_db, event)
        rollback_address_events(api_db, event)
        rollback_fairminters(api_db, event)

        sql = "DELETE FROM messages WHERE message_index = ?"
        delete_all(api_db, sql, (event["message_index"],))


def rollback_events(api_db, block_index):
    logger.debug(f"API Watcher - Rolling back events to block {block_index}...")
    # api_db.execute("""PRAGMA foreign_keys=OFF""")
    cursor = api_db.cursor()
    sql = "SELECT * FROM messages WHERE block_index >= ? ORDER BY message_index DESC"
    cursor.execute(sql, (block_index,))
    for event in cursor:
        rollback_event(api_db, event)
    # api_db.execute("""PRAGMA foreign_keys=ON""")
    logger.debug(f"API Watcher - Events rolled back to block {block_index}")


def update_balances(api_db, event):
    if event["event"] not in ["DEBIT", "CREDIT"]:
        return

    cursor = api_db.cursor()

    event_bindings = get_event_bindings(event)
    quantity = event_bindings["quantity"]
    if quantity == 0:
        return

    if event["event"] == "DEBIT":
        quantity = -quantity

    field_name = "address"
    address_or_utxo = event_bindings["address"]
    if "utxo" in event_bindings and event_bindings["utxo"]:
        field_name = "utxo"
        address_or_utxo = event_bindings["utxo"]
    event_bindings["address_or_utxo"] = address_or_utxo

    sql = f"SELECT * FROM balances WHERE {field_name} = :address_or_utxo AND asset = :asset"  # noqa: S608
    existing_balance = fetch_one(api_db, sql, event_bindings)

    if existing_balance is not None:
        sql = f"""
            UPDATE balances
            SET quantity = quantity + :quantity
            WHERE {field_name} = :address_or_utxo AND asset = :asset
            """  # noqa: S608
    else:
        sql = f"""
            INSERT INTO balances ({field_name}, asset, quantity, utxo_address)
            VALUES (:address_or_utxo, :asset, :quantity, :utxo_address)
            """  # noqa: S608
    utxo_address = None
    if "utxo_address" in event_bindings:
        utxo_address = event_bindings["utxo_address"]
    insert_bindings = {
        "address_or_utxo": address_or_utxo,
        "asset": event_bindings["asset"],
        "utxo_address": utxo_address,
        "quantity": quantity,
    }
    cursor.execute(sql, insert_bindings)


def rollback_balances(api_db, event):
    if event["event"] not in ["CREDIT", "DEBIT"]:
        return
    revert_event = event.copy()
    revert_event["event"] = "DEBIT" if event["event"] == "CREDIT" else "CREDIT"
    update_balances(api_db, revert_event)


def update_expiration(api_db, event):
    if event["event"] not in EXPIRATION_EVENTS_OBJECT_ID:
        return
    event_bindings = json.loads(event["bindings"])

    cursor = api_db.cursor()
    sql = """
        INSERT INTO all_expirations (object_id, block_index, type) 
        VALUES (:object_id, :block_index, :type)
        """
    bindings = {
        "object_id": event_bindings[EXPIRATION_EVENTS_OBJECT_ID[event["event"]]],
        "block_index": event_bindings["block_index"],
        "type": event["event"].replace("_EXPIRATION", "").lower(),
    }
    cursor.execute(sql, bindings)


def rollback_expiration(api_db, event):
    if event["event"] not in EXPIRATION_EVENTS_OBJECT_ID:
        return
    event_bindings = json.loads(event["bindings"])

    cursor = api_db.cursor()
    sql = """
        DELETE FROM all_expirations WHERE object_id = :object_id AND block_index = :block_index AND type = :type
        """
    bindings = {
        "object_id": event_bindings[EXPIRATION_EVENTS_OBJECT_ID[event["event"]]],
        "block_index": event_bindings["block_index"],
        "type": event["event"].replace("_EXPIRATION", "").lower(),
    }
    cursor.execute(sql, bindings)


def update_assets_info(api_db, event):
    if event["event"] not in ASSET_EVENTS:
        return
    event_bindings = json.loads(event["bindings"])

    event_bindings["confirmed"] = event["block_index"] != config.MEMPOOL_BLOCK_INDEX

    if event["event"] == "ASSET_CREATION":
        sql = """
            INSERT OR REPLACE INTO assets_info 
                (asset, asset_id, asset_longname, first_issuance_block_index, confirmed) 
            VALUES 
                (:asset_name, :asset_id, :asset_longname, :block_index, :confirmed)
            """
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return

    if event_bindings["status"] != "valid":
        return

    if event["event"] in ["ASSET_ISSUANCE", "RESET_ISSUANCE"]:
        existing_asset = fetch_one(
            api_db,
            "SELECT * FROM assets_info WHERE asset = :asset",
            {"asset": event_bindings["asset"]},
        )
        if existing_asset is None and "asset_longname" in event_bindings:
            existing_asset = fetch_one(
                api_db,
                "SELECT * FROM assets_info WHERE asset_longname = :asset_longname",
                {"asset_longname": event_bindings["asset_longname"]},
            )
        if existing_asset is not None and not event_bindings["confirmed"]:
            return
        set_data = []
        set_data.append("divisible = :divisible")
        set_data.append("description = :description")
        set_data.append("owner = :issuer")
        set_data.append("supply = supply + :quantity")
        set_data.append("last_issuance_block_index = :block_index")
        set_data.append("asset_longname = :asset_longname")
        set_data.append("confirmed = :confirmed")
        if event_bindings["locked"]:
            set_data.append("locked = :locked")
        if existing_asset is None or not existing_asset["issuer"]:  # first issuance
            set_data.append("issuer = :issuer")
        set_data = ", ".join(set_data)

        sql = f"UPDATE assets_info SET {set_data} WHERE asset = :asset"  # noqa: S608
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return

    if not event_bindings["confirmed"]:
        return

    if event["event"] == "ASSET_DESTRUCTION":
        sql = """
            UPDATE assets_info
            SET supply = supply - :quantity
            WHERE asset = :asset
            """
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return

    if event["event"] == "ASSET_TRANSFER":
        sql = """
            UPDATE assets_info 
            SET owner = :issuer
            WHERE asset = :asset
            """
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return

    if event["event"] == "BURN":
        sql = """
            UPDATE assets_info 
            SET supply = supply + :earned
            WHERE asset = 'XCP'
            """
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return


def refresh_assets_info(api_db, asset_name):
    issuances = fetch_all(
        api_db, "SELECT * FROM issuances WHERE asset = :asset", {"asset": asset_name}
    )

    if len(issuances) == 0:
        # will be delete by the ASSET_CREATION event rollback
        return

    set_fields = [
        "divisible",
        "description",
        "owner",
        "supply",
        "last_issuance_block_index",
        "asset_longname",
        "locked",
    ]
    set_data = ", ".join([f"{field} = :{field}" for field in set_fields])

    bindings = {
        "asset": asset_name,
        "divisible": issuances[-1]["divisible"],
        "description": issuances[-1]["description"],
        "owner": issuances[-1]["issuer"],
        "supply": ledger.asset_supply(api_db, asset_name),
        "last_issuance_block_index": issuances[-1]["block_index"],
        "asset_longname": issuances[-1]["asset_longname"],
        "locked": any(issuance["locked"] for issuance in issuances),
    }

    sql = f"UPDATE assets_info SET {set_data} WHERE asset = :asset"  # noqa: S608
    cursor = api_db.cursor()
    cursor.execute(sql, bindings)


def rollback_assets_info(api_db, event):
    if event["event"] not in ASSET_EVENTS:
        return
    event_bindings = json.loads(event["bindings"])

    if event["event"] == "ASSET_CREATION":
        sql = """
            DELETE FROM assets_info WHERE asset_id = ?
            """
        cursor = api_db.cursor()
        cursor.execute(sql, (event_bindings["asset_id"],))
        return

    if event_bindings["status"] != "valid":
        return

    if event["event"] == "ASSET_DESTRUCTION":
        sql = """
            UPDATE assets_info 
            SET supply = supply + :quantity
            WHERE asset = :asset
            """
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return

    if event["event"] == "BURN":
        sql = """
            UPDATE assets_info 
            SET supply = supply - :earned
            WHERE asset = 'XCP'
            """
        cursor = api_db.cursor()
        cursor.execute(sql, event_bindings)
        return

    # else
    refresh_assets_info(api_db, event_bindings["asset"])


def execute_event(api_db, event):
    cursor = api_db.cursor()
    sql, sql_bindings = event_to_sql(event)
    if sql is not None:
        # check if the transaction is already here from the mempool
        if event["event"] == "NEW_TRANSACTION":
            check_tx_sql = "SELECT * FROM transactions WHERE tx_hash = :tx_hash"
            if fetch_one(
                api_db,
                check_tx_sql,
                {"tx_hash": event["tx_hash"], "block_index": config.MEMPOOL_BLOCK_INDEX},
            ):
                clean_mempool(api_db)
        cursor.execute(sql, sql_bindings)
        if event["command"] == "insert":
            cursor.execute("SELECT last_insert_rowid() AS rowid")
            return cursor.fetchone()["rowid"]
    return None


def update_xcp_supply(api_db, event):
    if event["event"] not in XCP_DESTROY_EVENTS:
        return
    event_bindings = json.loads(event["bindings"])
    if "fee_paid" not in event_bindings:
        return
    if event_bindings["fee_paid"] == 0:
        return
    sql = """
        UPDATE assets_info 
        SET supply = supply - :fee_paid
        WHERE asset = 'XCP'
    """
    cursor = api_db.cursor()
    cursor.execute(sql, event_bindings)


def rollback_xcp_supply(api_db, event):
    if event["event"] not in XCP_DESTROY_EVENTS:
        return
    event_bindings = json.loads(event["bindings"])
    if "fee_paid" not in event_bindings:
        return
    if event_bindings["fee_paid"] == 0:
        return
    sql = """
        UPDATE assets_info 
        SET supply = supply + :fee_paid
        WHERE asset = 'XCP'
    """
    cursor = api_db.cursor()
    cursor.execute(sql, event_bindings)


def update_address_events(api_db, event):
    if event["event"] not in EVENTS_ADDRESS_FIELDS:
        return
    event_bindings = json.loads(event["bindings"])
    cursor = api_db.cursor()
    for field in EVENTS_ADDRESS_FIELDS[event["event"]]:
        sql = """
            INSERT INTO address_events (address, event_index)
            VALUES (:address, :event_index)
            """
        cursor.execute(
            sql, {"address": event_bindings[field], "event_index": event["message_index"]}
        )


def rollback_address_events(api_db, event):
    if event["event"] not in EVENTS_ADDRESS_FIELDS:
        return
    cursor = api_db.cursor()
    sql = "DELETE FROM address_events WHERE event_index = :message_index"
    cursor.execute(sql, event)


def update_fairminters(api_db, event):
    if event["event"] != "NEW_FAIRMINT":
        return
    event_bindings = json.loads(event["bindings"])
    if event_bindings["status"] != "valid":
        return
    cursor = api_db.cursor()
    sql = """
        UPDATE fairminters SET
            earned_quantity = COALESCE(earned_quantity, 0) + :earn_quantity,
            commission = COALESCE(commission, 0) + :commission,
            paid_quantity = COALESCE(paid_quantity, 0) + :paid_quantity
        WHERE tx_hash = :fairminter_tx_hash
    """
    cursor.execute(sql, event_bindings)


def rollback_fairminters(api_db, event):
    if event["event"] != "NEW_FAIRMINT":
        return
    event_bindings = json.loads(event["bindings"])
    if event_bindings["status"] != "valid":
        return
    cursor = api_db.cursor()
    sql = """
        UPDATE fairminters SET
            earned_quantity = earned_quantity - :earn_quantity,
            commission = commission - :commission,
            paid_quantity = paid_quantity - :paid_quantity
        WHERE tx_hash = :fairminter_tx_hash
    """
    cursor.execute(sql, event_bindings)


def parse_event(api_db, event, watcher, catching_up=False):
    if event["event"] in SKIP_EVENTS:
        event["insert_rowid"] = None
        insert_event(api_db, event)
        return
    with api_db:
        logger.trace(f"API Watcher - Parsing event: {event}")
        if event["event"] == "NEW_BLOCK":
            clean_mempool(api_db)
        event["insert_rowid"] = execute_event(api_db, event)
        update_balances(api_db, event)
        update_expiration(api_db, event)
        update_assets_info(api_db, event)
        update_xcp_supply(api_db, event)
        update_address_events(api_db, event)
        update_fairminters(api_db, event)
        insert_event(api_db, event)
        logger.event(f"API Watcher - Event parsed: {event['message_index']} {event['event']}")
        if event["event"] == "BLOCK_PARSED":
            synchronize_mempool(api_db, api_db, watcher.stop_event)


def catch_up(api_db, ledger_db, watcher):
    check_event_hashes(api_db, ledger_db)
    clean_mempool(api_db)
    event_to_parse_count = get_event_to_parse_count(api_db, ledger_db)
    if event_to_parse_count > 0:
        logger.debug(f"API Watcher - {event_to_parse_count} events to catch up...")
        start_time = time.time()
        event_parsed = 0
        next_event = get_next_event_to_parse(api_db, ledger_db)
        while next_event and not watcher.stop_event.is_set():
            parse_event(api_db, next_event, watcher, catching_up=True)
            event_parsed += 1
            if event_parsed % 50000 == 0:
                duration = time.time() - start_time
                logger.debug(
                    f"API Watcher - {event_parsed} / {event_to_parse_count} events parsed. ({format_duration(duration)})"
                )
            next_event = get_next_event_to_parse(api_db, ledger_db)
        if not watcher.stop_event.is_set():
            duration = time.time() - start_time
            logger.info(f"API Watcher - Catch up completed. ({format_duration(duration)})")
    else:
        logger.info("API Watcher - Catch up completed.")
    synchronize_mempool(api_db, api_db, watcher.stop_event)


def apply_migration():
    logger.debug("API Watcher - Applying migrations...")
    # Apply migrations
    backend = get_backend(f"sqlite:///{config.API_DATABASE}")
    migrations = read_migrations(MIGRATIONS_DIR)
    try:
        with backend.lock():
            # Apply any outstanding migrations
            backend.apply_migrations(backend.to_apply(migrations))
    except LockTimeout:
        logger.debug("API Watcher - Migration lock timeout. Breaking lock and retrying...")
        backend.break_lock()
        backend.apply_migrations(backend.to_apply(migrations))
    backend.connection.close()


def get_last_event(db):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages ORDER BY message_index DESC LIMIT 1")
    return cursor.fetchone()


def get_event(db, message_index):
    cursor = db.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_index = ?", (message_index,))
    return cursor.fetchone()


# checks that there is no divergence between the event in the API and ledger databases
def check_event_hashes(api_db, ledger_db):
    logger.trace("API Watcher - Checking event hashes...")
    last_api_event = get_last_event(api_db)
    if last_api_event is None:
        return
    ledger_event = get_event(ledger_db, last_api_event["message_index"])
    while ledger_event is None:
        rollback_event(api_db, last_api_event)
        last_api_event = get_last_event(api_db)
        ledger_event = get_event(ledger_db, last_api_event["message_index"])
    while (
        last_api_event
        and ledger_event
        and last_api_event["event_hash"] != ledger_event["event_hash"]
    ):
        rollback_event(api_db, last_api_event)
        last_api_event = get_last_event(api_db)
        ledger_event = get_event(ledger_db, last_api_event["message_index"])


def rollback(block_index):
    api_db = database.get_db_connection(config.API_DATABASE, read_only=False, check_wal=False)
    rollback_events(api_db, block_index)
    api_db.close()


def parse_next_event(api_db, ledger_db, watcher):
    check_event_hashes(api_db, ledger_db)

    last_event_sql = "SELECT * FROM messages ORDER BY message_index DESC LIMIT 1"
    last_ledger_event = fetch_one(ledger_db, last_event_sql)
    last_api_event = fetch_one(api_db, last_event_sql)

    if last_ledger_event is None:
        raise exceptions.NoEventToParse("No event to parse")

    if last_api_event is None:
        next_event_sql = "SELECT * FROM messages ORDER BY message_index ASC LIMIT 1"
        next_event = fetch_one(ledger_db, next_event_sql)
        parse_event(api_db, next_event, watcher)
        return next_event

    if last_ledger_event["message_index"] > last_api_event["message_index"]:
        next_event_sql = (
            "SELECT * FROM messages WHERE message_index > ? ORDER BY message_index ASC LIMIT 1"
        )
        next_event = fetch_one(ledger_db, next_event_sql, (last_api_event["message_index"],))
        parse_event(api_db, next_event, watcher)
        return next_event

    raise exceptions.NoEventToParse("No event to parse")


def clean_mempool(api_db):
    delete_all(api_db, "DELETE FROM mempool")
    for table in blocks.TABLES + ["transactions"]:
        delete_all(
            api_db,
            f"DELETE FROM {table} WHERE block_index = ?",  # noqa: S608
            (config.MEMPOOL_BLOCK_INDEX,),
        )
    delete_all(api_db, "DELETE FROM assets_info WHERE confirmed = ?", (False,))


def gen_random_tx_index(event):
    event_bindings = json.loads(event["bindings"])
    if "tx_index" in event_bindings:
        event_bindings["tx_index"] = event_bindings["tx_index"] * 1000 + randrange(  # noqa: S311
            99999999
        )
        event["bindings"] = json.dumps(event_bindings)
    return event


def synchronize_mempool(api_db, ledger_db, stop_event):
    if config.NO_MEMPOOL or stop_event.is_set():
        return
    logger.trace("API Watcher - Synchronizing mempool...")
    global MEMPOOL_SKIP_EVENT_HASHES  # noqa: PLW0602
    try:
        mempool_events = fetch_all(ledger_db, "SELECT * FROM mempool")
        sql_insert = """INSERT INTO mempool (tx_hash, command, category, bindings, event, timestamp, addresses) VALUES (?, ?, ?, ?, ?, ?, ?)"""
        with api_db:
            clean_mempool(api_db)
            cursor = api_db.cursor()
            for event in mempool_events:
                if stop_event.is_set():
                    logger.info("API Watcher - Stopping mempool synchronization due to stop event.")
                    break
                if event["event"] in SKIP_EVENTS + ["NEW_BLOCK", "BLOCK_PARSED"]:
                    continue
                if event["tx_hash"] in MEMPOOL_SKIP_EVENT_HASHES:
                    continue
                event_bindings = json.loads(event["bindings"])
                # edge case: asset already created in another confirmed tx
                if event["event"] == "ASSET_CREATION":
                    existing_asset = fetch_one(
                        api_db,
                        "SELECT * FROM assets WHERE asset_name = ?",
                        (event_bindings["asset_name"],),
                    )
                    if existing_asset is not None:
                        continue
                addresses = []
                if event["event"] in EVENTS_ADDRESS_FIELDS:
                    for field in EVENTS_ADDRESS_FIELDS[event["event"]]:
                        if field in event_bindings and event_bindings[field] is not None:
                            addresses.append(event_bindings[field])
                addresses = list(set(addresses))
                addresses = " ".join(addresses)

                bindings = [
                    event["tx_hash"],
                    event["command"],
                    event["category"],
                    event["bindings"],
                    event["event"],
                    event["timestamp"],
                    addresses,
                ]
                cursor.execute(sql_insert, bindings)
                event["block_index"] = config.MEMPOOL_BLOCK_INDEX
                event = gen_random_tx_index(event)  # noqa: PLW2901
                try:
                    execute_event(api_db, event)
                    update_assets_info(api_db, event)
                except apsw.ConstraintError as e:
                    if "UNIQUE constraint failed: transactions.tx_index" in str(e):
                        event = gen_random_tx_index(event)  # noqa: PLW2901
                        execute_event(api_db, event)
                        update_assets_info(api_db, event)
                    else:
                        # Skipping duplicate event
                        MEMPOOL_SKIP_EVENT_HASHES.append(event["tx_hash"])
                except Exception as e:
                    logger.error(f"API Watcher - Error executing mempool event: {e}")
                    raise e

            if len(mempool_events) > 0:
                logger.debug("API Watcher - %s mempool events synchronized", len(mempool_events))
    except apsw.SQLError:
        # TEMP: for testing
        pass


def refresh_xcp_supply(ledger_db, api_db):
    xcp_supply = ledger.xcp_supply(ledger_db)
    cursor = api_db.cursor()
    cursor.execute("UPDATE assets_info SET supply = ? WHERE asset = 'XCP'", (xcp_supply,))


class APIWatcher(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        logger.debug("Initializing API Watcher...")
        self.api_db = None
        self.ledger_db = None
        apply_migration()
        self.stop_event = threading.Event()  # Add stop event
        self.api_db = database.get_db_connection(
            config.API_DATABASE, read_only=False, check_wal=False
        )
        self.ledger_db = database.get_db_connection(
            config.DATABASE, read_only=True, check_wal=False
        )
        # Temporarily disable foreign keys for mempool transactions
        self.api_db.execute("PRAGMA foreign_keys=OFF")
        # Create XCP and BTC assets if they don't exist
        cursor = self.api_db.cursor()
        cursor.execute("""SELECT * FROM assets_info WHERE asset = ?""", ("XCP",))
        if not list(cursor):
            cursor.execute(
                """INSERT OR REPLACE INTO assets VALUES (?,?,?,?)""", ("0", "BTC", None, None)
            )
            cursor.execute(
                """INSERT OR REPLACE INTO assets VALUES (?,?,?,?)""", ("1", "XCP", None, None)
            )
            insert_asset_info_sql = """
                INSERT INTO assets_info (
                    asset, divisible, locked, supply, description,
                    first_issuance_block_index, last_issuance_block_index
                ) VALUES (
                    :asset, :divisible, :locked, :supply, :description,
                    :first_issuance_block_index, :last_issuance_block_index
                )
            """
            cursor.execute(
                insert_asset_info_sql,
                {
                    "asset": "XCP",
                    "divisible": True,
                    "locked": True,
                    "supply": ledger.xcp_supply(self.ledger_db),
                    "description": "The Counterparty protocol native currency",
                    "first_issuance_block_index": 278319,
                    "last_issuance_block_index": 283810,
                },
            )

        cursor.close()
        self.last_mempool_sync = 0

    def run(self):
        logger.info("Starting API Watcher thread...")
        catch_up(self.api_db, self.ledger_db, self)
        if not self.stop_event.is_set():
            self.follow()

    def follow(self):
        refresh_xcp_supply(self.ledger_db, self.api_db)
        while not self.stop_event.is_set():
            last_parsed_event = None
            try:
                last_parsed_event = parse_next_event(self.api_db, self.ledger_db, self)
            except exceptions.NoEventToParse:
                logger.trace("API Watcher - No new events to parse")
                self.stop_event.wait(timeout=0.1)
            if self.stop_event.is_set():
                break
            if time.time() - self.last_mempool_sync > 10 and (
                last_parsed_event is None or last_parsed_event["event"] == "BLOCK_PARSED"
            ):
                synchronize_mempool(self.api_db, self.ledger_db, self.stop_event)
                self.last_mempool_sync = time.time()

    def stop(self):
        logger.info("Stopping API Watcher thread...")
        self.stop_event.set()
        self.join()
        if self.api_db is not None:
            self.api_db.close()
        if self.ledger_db is not None:
            self.ledger_db.close()
        logger.info("API Watcher thread stopped.")
