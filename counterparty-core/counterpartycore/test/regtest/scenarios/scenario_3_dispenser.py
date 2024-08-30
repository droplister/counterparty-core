SCENARIO = [
    {
        "title": "Create Dispenser 1",
        "transaction": "dispenser",
        "source": "$ADDRESS_1",
        "params": {
            "asset": "XCP",
            "give_quantity": 1,
            "escrow_quantity": 10000,
            "mainchainrate": 1,  # 1 BTC for 1 XCP
            "status": 0,
        },
        "set_variables": {
            "DISPENSER_1_TX_HASH": "$TX_HASH",
        },
        "controls": [
            {
                "url": "blocks/131/events?event_name=OPEN_DISPENSER,DEBIT",
                "result": [
                    {
                        "event": "OPEN_DISPENSER",
                        "event_index": 162,
                        "params": {
                            "asset": "XCP",
                            "block_index": 131,
                            "dispense_count": 0,
                            "escrow_quantity": 10000,
                            "give_quantity": 1,
                            "give_remaining": 10000,
                            "oracle_address": None,
                            "origin": "$ADDRESS_1",
                            "satoshirate": 1,
                            "source": "$ADDRESS_1",
                            "status": 0,
                            "tx_hash": "$TX_HASH",
                            "tx_index": 18,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "DEBIT",
                        "event_index": 161,
                        "params": {
                            "action": "open dispenser",
                            "address": "$ADDRESS_1",
                            "asset": "XCP",
                            "block_index": 131,
                            "event": "$TX_HASH",
                            "quantity": 10000,
                            "tx_index": 18,
                            "utxo": None,
                            "utxo_address": None,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                ],
            }
        ],
    },
    {
        "title": "Dispense 1: get 900 XCP",
        "transaction": "dispense",
        "source": "$ADDRESS_2",
        "params": {
            "dispenser": "$ADDRESS_1",
            "quantity": 6000,
        },
        "controls": [
            {
                "url": "blocks/132/events?event_name=NEW_TRANSACTION,NEW_TRANSACTION_OUTPUT,CREDIT,DISPENSER_UPDATE,DISPENSE",
                "result": [
                    {
                        "event": "DISPENSE",
                        "event_index": 170,
                        "params": {
                            "asset": "XCP",
                            "block_index": 132,
                            "btc_amount": 6000,
                            "destination": "$ADDRESS_2",
                            "dispense_index": 0,
                            "dispense_quantity": 6000,
                            "dispenser_tx_hash": "$DISPENSER_1_TX_HASH",
                            "source": "$ADDRESS_1",
                            "tx_hash": "$TX_HASH",
                            "tx_index": 19,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "DISPENSER_UPDATE",
                        "event_index": 169,
                        "params": {
                            "asset": "XCP",
                            "dispense_count": 1,
                            "give_remaining": 4000,
                            "source": "$ADDRESS_1",
                            "status": 0,
                            "tx_hash": "$DISPENSER_1_TX_HASH",
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "CREDIT",
                        "event_index": 168,
                        "params": {
                            "address": "$ADDRESS_2",
                            "asset": "XCP",
                            "block_index": 132,
                            "calling_function": "dispense",
                            "event": "$TX_HASH",
                            "quantity": 6000,
                            "tx_index": 19,
                            "utxo": None,
                            "utxo_address": None,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "NEW_TRANSACTION_OUTPUT",
                        "event_index": 167,
                        "params": {
                            "block_index": 132,
                            "btc_amount": 6000,
                            "destination": "$ADDRESS_1",
                            "out_index": 0,
                            "tx_hash": "$TX_HASH",
                            "tx_index": 19,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "NEW_TRANSACTION",
                        "event_index": 166,
                        "params": {
                            "block_hash": "$BLOCK_HASH",
                            "block_index": 132,
                            "block_time": "$BLOCK_TIME",
                            "btc_amount": 6000,
                            "data": "0d00",
                            "destination": "$ADDRESS_1",
                            "fee": 0,
                            "source": "$ADDRESS_2",
                            "tx_hash": "$TX_HASH",
                            "tx_index": 19,
                            "utxos_info": "$TX_HASH:0",
                        },
                        "tx_hash": "$TX_HASH",
                    },
                ],
            }
        ],
    },
    {
        "title": "Dispense 2: get no enough asset error",
        "transaction": "dispense",
        "source": "$ADDRESS_3",
        "params": {
            "dispenser": "$ADDRESS_1",
            "quantity": 4001,
        },
        "expected_error": ["dispenser doesn't have enough asset to give"],
    },
    {
        "title": "Dispense 3: no dispenser error",
        "transaction": "dispense",
        "source": "$ADDRESS_3",
        "params": {
            "dispenser": "$ADDRESS_2",
            "quantity": 50,
        },
        "expected_error": ["address doesn't have any open dispenser"],
    },
    {
        "title": "Dispense 4: get 100 XCP",
        "transaction": "dispense",
        "source": "$ADDRESS_2",
        "params": {
            "dispenser": "$ADDRESS_1",
            "quantity": 4000,
        },
        "controls": [
            {
                "url": "blocks/133/events?event_name=NEW_TRANSACTION,NEW_TRANSACTION_OUTPUT,CREDIT,DISPENSER_UPDATE,DISPENSE",
                "result": [
                    {
                        "event": "DISPENSE",
                        "event_index": 178,
                        "params": {
                            "asset": "XCP",
                            "block_index": 133,
                            "btc_amount": 4000,
                            "destination": "$ADDRESS_2",
                            "dispense_index": 0,
                            "dispense_quantity": 4000,
                            "dispenser_tx_hash": "$DISPENSER_1_TX_HASH",
                            "source": "$ADDRESS_1",
                            "tx_hash": "$TX_HASH",
                            "tx_index": 20,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "DISPENSER_UPDATE",
                        "event_index": 177,
                        "params": {
                            "asset": "XCP",
                            "dispense_count": 2,
                            "give_remaining": 0,
                            "source": "$ADDRESS_1",
                            "status": 10,
                            "tx_hash": "$DISPENSER_1_TX_HASH",
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "CREDIT",
                        "event_index": 176,
                        "params": {
                            "address": "$ADDRESS_2",
                            "asset": "XCP",
                            "block_index": 133,
                            "calling_function": "dispense",
                            "event": "$TX_HASH",
                            "quantity": 4000,
                            "tx_index": 20,
                            "utxo": None,
                            "utxo_address": None,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "NEW_TRANSACTION_OUTPUT",
                        "event_index": 175,
                        "params": {
                            "block_index": 133,
                            "btc_amount": 4000,
                            "destination": "$ADDRESS_1",
                            "out_index": 0,
                            "tx_hash": "$TX_HASH",
                            "tx_index": 20,
                        },
                        "tx_hash": "$TX_HASH",
                    },
                    {
                        "event": "NEW_TRANSACTION",
                        "event_index": 174,
                        "params": {
                            "block_hash": "$BLOCK_HASH",
                            "block_index": 133,
                            "block_time": "$BLOCK_TIME",
                            "btc_amount": 4000,
                            "data": "0d00",
                            "destination": "$ADDRESS_1",
                            "fee": 0,
                            "source": "$ADDRESS_2",
                            "tx_hash": "$TX_HASH",
                            "tx_index": 20,
                            "utxos_info": "$TX_HASH:0",
                        },
                        "tx_hash": "$TX_HASH",
                    },
                ],
            }
        ],
    },
    {
        "title": "Dispense 5: dispenser is closed and empy errors",
        "transaction": "dispense",
        "source": "$ADDRESS_3",
        "params": {
            "dispenser": "$ADDRESS_1",
            "quantity": 4001,
        },
        "expected_error": ["dispenser is not open", "dispenser is empty"],
    },
    {
        "title": "Create Dispenser 2: dispenser must be created by source",
        "transaction": "dispenser",
        "source": "$ADDRESS_1",
        "params": {
            "asset": "XCP",
            "give_quantity": 1,
            "escrow_quantity": 10000,
            "mainchainrate": 1,  # 1 BTC for 1 XCP
            "status": 0,
            "open_address": "$ADDRESS_5",
        },
        "expected_error": ["dispenser must be created by source"],
    },
]
