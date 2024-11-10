import math

from counterpartycore.lib import config, database, ledger, util

PERIOD_DURATION = 2016  # blocks, around 2 weeks


def initialise(db):
    cursor = db.cursor()
    # transaction_count
    cursor.execute(
        """CREATE TABLE IF NOT EXISTS transaction_count(
            block_index INTEGER,
            transaction_id INTEGER,
            count INTEGER)
        """
    )
    database.create_indexes(
        cursor,
        "transaction_count",
        [
            ["block_index", "transaction_id"],
        ],
    )


def get_transaction_count_by_block(db, transaction_id, block_index):
    cursor = db.cursor()
    sql = """
    SELECT count FROM transaction_count
    WHERE transaction_id = ? AND block_index == ?
    ORDER BY rowid DESC
    LIMIT 1
    """  # noqa S608
    binding = (transaction_id, block_index)
    count = cursor.execute(sql, binding).fetchone()
    if count is None:
        return 0
    return count["count"]


def increment_counter(db, transaction_id, block_index):
    current_count = get_transaction_count_by_block(db, transaction_id, block_index)
    new_count = current_count + 1

    bindings = {
        "block_index": block_index,
        "transaction_id": transaction_id,
        "count": new_count,
    }
    ledger.insert_record(db, "transaction_count", bindings, "INCREMENT_TRANSACTION_COUNT")


def get_transaction_count_for_last_period(db, transaction_id, block_index):
    if block_index < PERIOD_DURATION:
        return 0

    cursor = db.cursor()
    sql = """
        SELECT SUM(count) as total_count FROM (
            SELECT MAX(rowid) as rowid, block_index, count
            FROM transaction_count
            WHERE transaction_id = ?
            AND block_index >= ? AND block_index < ?
            GROUP BY block_index
        )
    """
    bindings = (transaction_id, block_index - PERIOD_DURATION, block_index)
    transaction_count = cursor.execute(sql, bindings).fetchone()
    if transaction_count is None:
        return 0
    return transaction_count["total_count"] or 0


def get_average_transactions(db, transaction_id, block_index):
    transaction_count = get_transaction_count_for_last_period(db, transaction_id, block_index)
    if transaction_count == 0:
        return 0
    # return average number of transactions for the last PERIOD_DURATION blocks
    return transaction_count // PERIOD_DURATION


def get_transaction_fee(db, transaction_id, block_index):
    print("get_transaction_fee")

    x = get_average_transactions(db, transaction_id, block_index)
    fee_params = util.get_value_by_block_index("fee_parameters", block_index)

    if fee_params is None:
        return 0

    a = fee_params[str(transaction_id)]["fee_lower_threshold"]
    b = fee_params[str(transaction_id)]["fee_upper_threshold"]
    base_fee = fee_params[str(transaction_id)]["base_fee"]
    k = fee_params[str(transaction_id)]["fee_sigmoid_k"]

    fee = calculate_fee(x, a, b, base_fee, k)
    return int(fee * config.UNIT)


def calculate_fee(x, a, b, base_fee, k):
    """
    Calculate the fee based on the number of transactions per block,
    ensuring continuity at the transition point.

    Parameters:
    x (float): Number of transactions per period
    a (float): Lower threshold (fee is zero below this)
    b (float): Upper threshold (transition point to exponential growth)
    base_fee (float): Base fee amount
    k (float): Sigmoid steepness factor

    Returns:
    float: Calculated fee
    """
    m = 1.5  # Exponent
    n = 100  # Exponential Scaling Factor

    def sigmoid(t):
        midpoint = (b - a) / 2 + a
        return base_fee / (1 + math.exp(-k * (t - midpoint)))

    if x <= a:
        return 0
    elif x <= b:
        return sigmoid(x)
    else:
        return base_fee + (((x - b) ** m) / n)
