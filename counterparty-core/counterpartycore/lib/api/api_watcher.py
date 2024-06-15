import json
import logging
import os
import time
from threading import Thread

from counterpartycore.lib import config, database

logger = logging.getLogger(config.LOGGER_NAME)

CURRENT_DIR = os.path.dirname(os.path.realpath(__file__))
MIGRATIONS_FILE = os.path.join(CURRENT_DIR, "migrations", "0001.create-api-database.sql")

UPDATE_EVENTS_ID_FIELDS = {
    "BLOCK_PARSED": ["block_index"],
    "TRANSACTION_PARSED": ["tx_index"],
    "BET_MATCH_UPDATE": ["id"],
    "BET_UPDATE": ["tx_hash"],
    "DISPENSER_UPDATE": ["source", "asset"],
    "ORDER_FILLED": ["tx_hash"],
    "ORDER_MATCH_UPDATE": ["id"],
    "ORDER_UPDATE": ["tx_hash"],
    "RPS_MATCH_UPDATE": ["id"],
    "RPS_UPDATE": ["tx_hash"],
}

EXPIRATION_EVENTS_OBJECT_ID = {
    "ORDER_EXPIRATION": "order_hash",
    "ORDER_MATCH_EXPIRATION": "order_match_id",
    "RPS_EXPIRATION": "rps_hash",
    "RPS_MATCH_EXPIRATION": "rps_match_id",
    "BET_EXPIRATION": "bet_hash",
    "BET_MATCH_EXPIRATION": "bet_match_id",
}


def get_last_parsed_message_index(api_db):
    cursor = api_db.cursor()
    sql = "SELECT * FROM messages ORDER BY message_index DESC LIMIT 1"
    cursor.execute(sql)
    last_event = cursor.fetchone()
    last_message_index = -1
    if last_event:
        last_message_index = last_event["message_index"]
    return last_message_index


def get_next_event_to_parse(api_db, ledger_db):
    last_parsed_message_index = get_last_parsed_message_index(api_db)
    cursor = ledger_db.cursor()
    sql = "SELECT * FROM messages WHERE message_index > ? ORDER BY message_index ASC LIMIT 1"
    cursor.execute(sql, (last_parsed_message_index,))
    next_event = cursor.fetchone()
    return next_event


def get_event_to_parse_count(api_db, ledger_db):
    last_parsed_message_index = get_last_parsed_message_index(api_db)
    cursor = ledger_db.cursor()
    sql = "SELECT message_index FROM messages ORDER BY message_index DESC LIMIT 1"
    cursor.execute(sql)
    last_event = cursor.fetchone()
    return last_event["message_index"] - last_parsed_message_index


def get_event_bindings(event):
    event_bindings = json.loads(event["bindings"])
    if "order_match_id" in event_bindings:
        del event_bindings["order_match_id"]
    elif event["category"] == "dispenses" and "btc_amount" in event_bindings:
        del event_bindings["btc_amount"]
    return event_bindings


def insert_event_to_sql(event):
    event_bindings = get_event_bindings(event)
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
    sql_bindings = []
    sql = f"UPDATE {event['category']} SET "  # noqa: S608
    id_field_names = UPDATE_EVENTS_ID_FIELDS[event["event"]]
    for key, value in event_bindings.items():
        if key in id_field_names:
            continue
        sql += f"{key} = ?, "
        sql_bindings.append(value)
    sql = sql[:-2]  # remove trailing comma
    sql += " WHERE "
    for id_field_name in id_field_names:
        sql += f"{id_field_name} = ? AND "
        sql_bindings.append(event_bindings[id_field_name])
    sql = sql[:-5]  # remove trailing " AND "
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
        cursor = api_db.cursor()
        id_field_names = UPDATE_EVENTS_ID_FIELDS[event["event"]]
        sql = f"SELECT * FROM {event['category']} WHERE "  # noqa: S608
        for id_field_name in id_field_names:
            sql += f"{id_field_name} = ? AND "
        sql = sql[:-5]  # remove trailing " AND "
        event_bindings = json.loads(event["bindings"])
        cursor.execute(sql, event_bindings)
        previous_state = cursor.fetchone()
    return previous_state


def delete_event(api_db, event):
    bindings = get_event_bindings(event)
    sql = f"DELETE FROM {event['category']} WHERE "  # noqa: S608
    for field_name in bindings:
        sql += f"{field_name} = :{field_name} AND "
    sql = sql[:-5]  # remove trailing " AND "
    cursor = api_db.cursor()
    cursor.execute(sql, bindings)


def insert_event(api_db, event):
    event["previous_state"] = json.dumps(get_event_previous_state(api_db, event))
    sql = """
        INSERT INTO messages 
            (message_index, block_index, event, category, command, bindings, tx_hash, previous_state)
        VALUES (:message_index, :block_index, :event, :category, :command, :bindings, :tx_hash, :previous_state)
    """
    cursor = api_db.cursor()
    cursor.execute(sql, event)


def rollback_event(api_db, event):
    if event["previous_state"] is None:
        delete_event(api_db, event)
        return
    previous_state = json.loads(event["previous_state"])

    sql = f"UPDATE {event['category']} SET "  # noqa: S608
    id_field_names = UPDATE_EVENTS_ID_FIELDS[event["event"]]
    for key in previous_state.keys():
        if key in id_field_names:
            continue
        sql += f"{key} = :{key}, "
    sql = sql[:-2]  # remove trailing comma
    sql += " WHERE "
    for id_field_name in id_field_names:
        sql += f"{id_field_name} = :{id_field_name} AND "
    sql = sql[:-5]  # remove trailing " AND "

    cursor = api_db.cursor()
    cursor.execute(sql, previous_state)


def rollback_events(api_db, block_index):
    with api_db:
        cursor = api_db.cursor()
        sql = "SELECT * FROM messages WHERE block_index >= ?"
        cursor.execute(sql, (block_index,))
        events = cursor.fetchall()
        for event in events:
            rollback_event(api_db, event)
        cursor.execute("DELETE FROM messages WHERE block_index >= ?", (block_index,))


def update_balances(api_db, event):
    if event["event"] not in ["DEBIT", "CREDIT"]:
        return

    cursor = api_db.cursor()

    event_bindings = get_event_bindings(event)
    quantity = event_bindings["quantity"]
    if event["event"] == "DEBIT":
        quantity = -quantity

    existing_balance = cursor.execute(
        "SELECT * FROM balances WHERE address = :address AND asset = :asset",
        event_bindings,
    ).fetchone()

    if existing_balance is None:
        sql = """
            UPDATE balances
            SET quantity = quantity + :quantity
            WHERE address = :address AND asset = :asset
            """
    else:
        sql = """
            INSERT INTO balances (address, asset, quantity)
            VALUES (:address, :asset, :quantity)
            """
    insert_bindings = {
        "address": event_bindings["address"],
        "asset": event_bindings["asset"],
        "quantity": quantity,
    }
    cursor.execute(sql, insert_bindings)


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


def execute_event(api_db, event):
    sql, sql_bindings = event_to_sql(event)
    if sql is not None:
        cursor = api_db.cursor()
        cursor.execute(sql, sql_bindings)


def parse_event(api_db, event, initial_parsing=False):
    initial_event_saved = [
        "NEW_BLOCK",
        "NEW_TRANSACTION",
        "BLOCK_PARSED",
        "TRANSACTION_PARSED",
        "CREDIT",
        "DEBIT",
    ]
    with api_db:
        if not (initial_parsing and event["event"] in initial_event_saved):
            execute_event(api_db, event)
            update_balances(api_db, event)
        update_expiration(api_db, event)
        insert_event(api_db, event)
        logger.trace(f"Event parsed: {event['message_index']} {event['event']}")


def copy_table(api_db, ledger_db, table_name, last_block_index, group_by=None):
    logger.debug(f"Copying table {table_name}...")
    start_time = time.time()
    ledger_cursor = ledger_db.cursor()

    if group_by:
        select_sql = f"SELECT *, MAX(rowid) AS rowid FROM {table_name} WHERE block_index <= ? GROUP BY {group_by}"  # noqa: S608
    else:
        select_sql = f"SELECT * FROM {table_name} WHERE block_index <= ?"  # noqa: S608
    ledger_cursor.execute(f"{select_sql} LIMIT 1", (last_block_index,))
    first_row = ledger_cursor.fetchone()

    ledger_cursor.execute(f"SELECT COUNT(*) AS count FROM ({select_sql})", (last_block_index,))  # noqa: S608
    total_rows = ledger_cursor.fetchone()["count"]

    field_names = ", ".join(first_row.keys())
    field_values = ", ".join([f":{key}" for key in first_row.keys()])
    insert_sql = f"INSERT INTO {table_name} ({field_names}) VALUES ({field_values})"  # noqa: S608

    ledger_cursor.execute(select_sql, (last_block_index,))  # noqa: S608
    saved_rows = 0
    with api_db:
        api_cursor = api_db.cursor()
        for row in ledger_cursor:
            api_cursor.execute(insert_sql, row)
            saved_rows += 1
            if saved_rows % 100000 == 0:
                logger.debug(f"{saved_rows}/{total_rows} rows of {table_name} copied")

    duration = time.time() - start_time
    logger.debug(f"Table {table_name} copied in {duration:.2f} seconds")


def initial_events_parsing(api_db, ledger_db, last_block_index):
    logger.debug("Initial event parsing...")
    start_time = time.time()
    ledger_cursor = ledger_db.cursor()

    ledger_cursor.execute(
        "SELECT COUNT(*) AS count FROM messages WHERE block_index <= ?", (last_block_index,)
    )
    event_count = ledger_cursor.fetchone()["count"]

    ledger_cursor.execute(
        "SELECT * FROM messages WHERE block_index <= ? ORDER BY message_index ASC",
        (last_block_index,),
    )
    parsed_event_count = 0
    for event in ledger_cursor:
        parse_event(api_db, event, initial_parsing=True)
        parsed_event_count += 1
        if parsed_event_count % 100000 == 0:
            duration = time.time() - start_time
            expected_duration = duration / parsed_event_count * event_count
            logger.debug(
                f"{parsed_event_count}/{event_count} events parsed in {duration:.2f} seconds (expected {expected_duration:.2f} seconds)"
            )

    duration = time.time() - start_time
    logger.debug(f"Initial event parsing completed in {duration:.2f} seconds")


def catch_up(api_db, ledger_db):
    event_to_parse_count = get_event_to_parse_count(api_db, ledger_db)
    if event_to_parse_count > 0:
        logger.info(f"{event_to_parse_count} events to catch up...")
        start_time = time.time()
        event_parsed = 0
        next_event = get_next_event_to_parse(api_db, ledger_db)
        while next_event:
            logger.trace(f"Parsing event: {next_event}")
            parse_event(api_db, next_event)
            event_parsed += 1
            if event_parsed % 10000 == 0:
                duration = time.time() - start_time
                expected_duration = duration / event_parsed * event_to_parse_count
                logger.info(
                    f"{event_parsed}/{event_to_parse_count} events parsed in {duration:.2f} seconds (expected {expected_duration:.2f} seconds)"
                )
            next_event = get_next_event_to_parse(api_db, ledger_db)
        duration = time.time() - start_time
        logger.info(f"{event_parsed} events parsed in {duration:.2f} seconds")


def optimized_catch_up(api_db, ledger_db):
    # check last parsed message index
    last_message_index = get_last_parsed_message_index(api_db)
    if last_message_index == -1:
        logger.info("New API database, initializing...")
        start_time = time.time()
        sql = "SELECT MAX(block_index) AS block_index FROM messages"
        last_block_index = ledger_db.cursor().execute(sql).fetchone()["block_index"]
        with api_db:  # everything or nothing
            for table in ["blocks", "transactions", "credits", "debits"]:
                copy_table(api_db, ledger_db, table, last_block_index)
            copy_table(api_db, ledger_db, "balances", last_block_index, group_by="address, asset")
            initial_events_parsing(api_db, ledger_db, last_block_index)
        duration = time.time() - start_time
        logger.info(f"API database initialized in {duration:.2f} seconds")


def initialize_api_db(api_db, ledger_db):
    logger.info("Initializing API Database...")

    cursor = api_db.cursor()

    # TODO: use migrations library
    with open(MIGRATIONS_FILE, "r") as f:
        sql = f.read()
        cursor.execute(sql)

    # Create XCP and BTC assets if they don't exist
    cursor.execute("""SELECT * FROM assets WHERE asset_name = ?""", ("BTC",))
    if not list(cursor):
        cursor.execute("""INSERT INTO assets VALUES (?,?,?,?)""", ("0", "BTC", None, None))
        cursor.execute("""INSERT INTO assets VALUES (?,?,?,?)""", ("1", "XCP", None, None))
    cursor.close()

    # catch_up(api_db, ledger_db)
    optimized_catch_up(api_db, ledger_db)


def get_last_block(api_db):
    cursor = api_db.cursor()
    cursor.execute("SELECT * FROM blocks ORDER BY block_index DESC LIMIT 1")
    return cursor.fetchone()


class APIWatcher(Thread):
    def __init__(self):
        logger.debug("Initializing API Watcher...")
        self.stopping = False
        self.stopped = False
        self.api_db = database.get_db_connection(
            config.API_DATABASE, read_only=False, check_wal=False
        )
        self.ledger_db = database.get_db_connection(
            config.DATABASE, read_only=True, check_wal=False
        )

        initialize_api_db(self.api_db, self.ledger_db)

        Thread.__init__(self)

    def run(self):
        logger.info("Starting API Watcher...")
        while True and not self.stopping:
            next_event = get_next_event_to_parse(self.api_db, self.ledger_db)
            if next_event:
                logger.trace(f"Parsing event: {next_event}")
                last_block = get_last_block(self.api_db)
                if last_block and last_block["block_index"] > next_event["block_index"]:
                    rollback_events(self.api_db, next_event["block_index"])
                parse_event(self.api_db, next_event)
            else:
                logger.trace("No new events to parse")
                time.sleep(1)
        self.stopped = True
        return

    def stop(self):
        logger.info("Stopping API Watcher...")
        self.stopping = True
        while not self.stopped:
            time.sleep(0.1)
        self.api_db.close()
        self.ledger_db.close()
        logger.trace("API Watcher stopped")
