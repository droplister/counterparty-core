import logging
import os
import time
import platform
import signal

from counterpartylib import server
from counterpartylib.lib import config, blocks, ledger, backend, database
from counterpartylib.lib.kickstart.blocks_parser import BlockchainParser, ChainstateParser

logger = logging.getLogger(__name__)


def fetch_blocks(db, bitcoind_dir, last_known_hash):
    block_parser = BlockchainParser(bitcoind_dir)
    cursor = db.cursor()
    start_time_blocks_indexing = time.time()
    # save blocks from last to first
    current_hash = last_known_hash
    lot_size = 100
    block_count = 0
    while True:
        bindings_lot = ()
        bindings_place = []
        # gather some blocks
        while len(bindings_lot) <= lot_size * 4:
            # read block from bitcoind files
            block = block_parser.read_raw_block(current_hash, only_header=True)
            # prepare bindings
            bindings_lot += (block['block_index'], block['block_hash'], block['block_time'], block['hash_prev'], block['bits'])
            bindings_place.append('(?,?,?,?,?)')
            current_hash = block['hash_prev']
            block_count += 1
            if block['block_index'] == config.BLOCK_FIRST:
                break
        # insert blocks by lot
        cursor.execute(f'''INSERT INTO blocks (block_index, block_hash, block_time, previous_block_hash, difficulty)
                              VALUES {', '.join(bindings_place)}''',
                              bindings_lot)
        print(f"Block {bindings_lot[0]} to {bindings_lot[-5]} inserted.", end="\r")
        if block['block_index'] == config.BLOCK_FIRST:
            break
    block_parser.close()
    logger.info('Blocks indexed in: {:.3f}s'.format(time.time() - start_time_blocks_indexing))
    return block_count


def prepare_db_for_resume(db, resume_from):
    # get block count
    cursor = db.cursor()
    cursor.execute('''SELECT block_index FROM blocks ORDER BY block_index DESC LIMIT 1''')
    last_block_index = cursor.fetchone()['block_index']
    # clean tables from resume block
    if resume_from != 'last':
        resume_block_index = int(resume_from)
        for table in blocks.TABLES + ['transaction_outputs', 'transactions']:
            blocks.clean_table_from(cursor, table, resume_block_index)
    # get last parsed transaction
    cursor.execute('''SELECT block_index, tx_index FROM transactions ORDER BY block_index DESC, tx_index DESC LIMIT 1''')
    last_transaction = cursor.fetchone()
    last_parsed_block = config.BLOCK_FIRST - 1
    tx_index = 0
    if last_transaction is not None:
        last_parsed_block = last_transaction['block_index']
        tx_index = last_transaction['tx_index'] + 1
    # clean tables from last parsed block
    for table in blocks.TABLES:
        blocks.clean_table_from(cursor, table, last_parsed_block)
    # clean hashes
    if resume_from != 'last':
        cursor.execute('''UPDATE blocks
                                    SET txlist_hash = :txlist_hash, 
                                        ledger_hash = :ledger_hash,
                                        messages_hash = :messages_hash
                                    WHERE block_index > :block_index''', {
                                    'txlist_hash': None,
                                    'ledger_hash': None,
                                    'messages_hash': None,
                                    'block_index': last_parsed_block
                                })

    block_count = last_block_index - last_parsed_block

    logger.info(f"Resuming from block {last_parsed_block}...")

    return block_count, tx_index, last_parsed_block


def run(bitcoind_dir, force=False, last_hash=None, resume_from=None, max_queue_size=None, debug_block=None):

    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)

    if debug_block is not None:
        resume_from = int(debug_block) - 1

    ledger.CURRENT_BLOCK_INDEX = 0
    backend.BACKEND()
    check_address = "tb1qurdetpdk8zg2thzx3g77qkgr7a89cp2m429t9c" if config.TESTNET else "34qkc2iac6RsyxZVfyE2S5U5WcRsbg2dpK"
    check_addrindexrs = backend.get_oldest_tx(check_address)
    print("check_addrindexrs: ", check_addrindexrs)
    assert check_addrindexrs != {}

    # determine bitoincore data directory
    if bitcoind_dir is None:
        if platform.system() == 'Darwin':
            bitcoind_dir = os.path.expanduser('~/Library/Application Support/Bitcoin/')
        elif platform.system() == 'Windows':
            bitcoind_dir = os.path.join(os.environ['APPDATA'], 'Bitcoin')
        else:
            bitcoind_dir = os.path.expanduser('~/.bitcoin')
    if not os.path.isdir(bitcoind_dir):
        raise Exception('Bitcoin Core data directory not found at {}. Use --bitcoind-dir parameter.'.format(bitcoind_dir))

    default_queue_size = 100
    if config.TESTNET:
        bitcoind_dir = os.path.join(bitcoind_dir, 'testnet3')
        default_queue_size = 1000

    warnings = [
        f'- `{config.DATABASE}` will be moved to `{config.DATABASE}.old` and recreated from scratch.',
        '- Ensure `addrindexrs` is running and up to date.',
        '- Ensure that `bitcoind` is stopped.',
        '- The initialization may take a while.',
    ]
    if resume_from is not None:
        warnings.pop(0)
    message = "\n" + "\n".join(warnings)
    logger.warning(f'''Warning:{message}''')

    if not force and input('Proceed with the initialization? (y/N) : ') != 'y':
        return

    start_time_total = time.time()

    # Get hash of last known block.
    last_known_hash = last_hash
    if last_known_hash is None:
        chain_parser = ChainstateParser(os.path.join(bitcoind_dir, 'chainstate'))
        last_known_hash = chain_parser.get_last_block_hash()
        chain_parser.close()
    logger.info('Last known block hash: {}'.format(last_known_hash))

    # backup old database
    if os.path.exists(config.DATABASE) and resume_from is None:
        os.rename(config.DATABASE, config.DATABASE + '.old')

    # initialise database
    kickstart_db = server.initialise_db()
    cursor = kickstart_db.cursor()
    cursor.execute('PRAGMA auto_vacuum = 1')
    cursor.execute('PRAGMA journal_mode = PERSIST')
    cursor.close()

    blocks.initialise(kickstart_db)

    if os.path.exists(config.DATABASE) and resume_from is not None:
        block_count, tx_index, last_parsed_block = prepare_db_for_resume(
            kickstart_db, resume_from
        )
    else:
        database.update_version(kickstart_db)
        # fill `blocks`` table from bitcoind files
        block_count = fetch_blocks(kickstart_db, bitcoind_dir, last_known_hash)
        last_parsed_block = 0
        tx_index = 0

    # Start block parser.
    queue_size = max_queue_size if max_queue_size is not None else default_queue_size
    block_parser = BlockchainParser(bitcoind_dir, config.DATABASE, last_parsed_block, queue_size)

    try:
        # save transactions for each blocks from first to last
        # then parse the block
        start_time_all_blocks_parse = time.time()
        block_parsed_count = 0
        block = block_parser.next_block()
        while block is not None:
            start_time_block_parse = time.time()
            ledger.CURRENT_BLOCK_INDEX = block['block_index']
            with kickstart_db: # ensure all the block or nothing
                # save transactions
                for transaction in block['transactions']:
                    # Cache transaction. We do that here because the block is fetched by another process.
                    block_parser.put_in_cache(transaction)
                    tx_index = blocks.list_tx(kickstart_db,
                                            block['block_hash'],
                                            block['block_index'],
                                            block['block_time'],
                                            transaction['tx_hash'],
                                            tx_index,
                                            decoded_tx=transaction,
                                            block_parser=block_parser)
                # Parse the transactions in the block.
                blocks.parse_block(kickstart_db, block['block_index'], block['block_time'])
            last_parsed_block = block['block_index']
            if block['block_hash'] == last_known_hash:
                break
            # let's have a nice message
            block_parsed_count += 1
            block_parsing_duration = time.time() - start_time_block_parse
            message = f"Block {block['block_index']} parsed in {block_parsing_duration:.3f}s."
            message += f" {tx_index} transactions indexed."
            cumulated_duration = time.time() - start_time_all_blocks_parse
            message += f" Cumulated duration: {cumulated_duration:.3f}s."
            expected_duration = (cumulated_duration / block_parsed_count) * block_count
            message += f" Expected duration: {expected_duration:.3f}s."
            print(message, end="\r")
            # get next block
            block_parser.block_parsed()
            if debug_block is not None and block['block_index'] == int(debug_block):
                block = None
            else:
                block = block_parser.next_block()
        logger.info('All blocks parsed in: {:.3f}s'.format(time.time() - start_time_all_blocks_parse))
    except KeyboardInterrupt:
        logger.warning('Keyboard interrupt. Stopping...')
    finally:
        backend.stop()
        block_parser.close()
        logger.info("Last parsed block: {}".format(last_parsed_block))

    logger.info('Kickstart done in: {:.3f}s'.format(time.time() - start_time_total))
