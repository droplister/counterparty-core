from counterpartycore.lib import transaction
from counterpartycore.lib.api import queries, util
from counterpartycore.lib.backend import addrindexrs, bitcoind

# Define the API routes except root (`/`) defined in `api_server.py`
ROUTES = util.prepare_routes(
    {
        ### /blocks ###
        "/v2/blocks": queries.get_blocks,
        "/v2/blocks/last": queries.get_last_block,
        "/v2/blocks/<int:block_index>": queries.get_block_by_height,
        "/v2/blocks/<block_hash>": queries.get_block_by_hash,
        "/v2/blocks/<int:block_index>/transactions": queries.get_transactions_by_block,
        "/v2/blocks/<int:block_index>/events": queries.get_events_by_block,
        "/v2/blocks/<int:block_index>/events/counts": queries.get_event_counts_by_block,
        "/v2/blocks/<int:block_index>/events/<event>": queries.get_events_by_block_and_event,
        "/v2/blocks/<int:block_index>/credits": queries.get_credits_by_block,
        "/v2/blocks/<int:block_index>/debits": queries.get_debits_by_block,
        "/v2/blocks/<int:block_index>/expirations": queries.get_expirations,
        "/v2/blocks/<int:block_index>/cancels": queries.get_cancels,
        "/v2/blocks/<int:block_index>/destructions": queries.get_destructions,
        "/v2/blocks/<int:block_index>/issuances": queries.get_issuances_by_block,
        "/v2/blocks/<int:block_index>/sends": queries.get_sends_by_block,
        "/v2/blocks/<int:block_index>/dispenses": queries.get_dispenses_by_block,
        "/v2/blocks/<int:block_index>/sweeps": queries.get_sweeps_by_block,
        ### /transactions ###
        "/v2/transactions": queries.get_transactions,
        "/v2/transactions/info": transaction.info,
        "/v2/transactions/unpack": transaction.unpack,
        "/v2/transactions/<int:tx_index>": queries.get_transaction_by_tx_index,
        "/v2/transactions/<tx_hash>": queries.get_transaction_by_hash,
        "/v2/transactions/<int:tx_index>/events": queries.get_events_by_transaction_index,
        "/v2/transactions/<tx_hash>/events": queries.get_events_by_transaction_hash,
        "/v2/transactions/<tx_hash>/sends": queries.get_sends_by_transaction_hash,
        "/v2/transactions/<tx_hash>/dispenses": queries.get_dispenses_by_transaction_hash,
        "/v2/transactions/<int:tx_index>/events/<event>": queries.get_events_by_transaction_index_and_event,
        "/v2/transactions/<tx_hash>/events/<event>": queries.get_events_by_transaction_hash_and_event,
        ### /addresses ###
        "/v2/addresses/balances": queries.get_balances_by_addresses,
        "/v2/addresses/transactions": queries.get_transactions_by_addresses,
        "/v2/addresses/events": queries.get_events_by_addresses,
        "/v2/addresses/mempool": queries.get_mempool_events_by_addresses,
        "/v2/addresses/<address>/balances": queries.get_address_balances,
        "/v2/addresses/<address>/balances/<asset>": queries.get_balance_by_address_and_asset,
        "/v2/addresses/<address>/credits": queries.get_credits_by_address,
        "/v2/addresses/<address>/debits": queries.get_debits_by_address,
        "/v2/addresses/<address>/bets": queries.get_bet_by_feed,
        "/v2/addresses/<address>/broadcasts": queries.get_broadcasts_by_source,
        "/v2/addresses/<address>/burns": queries.get_burns_by_address,
        "/v2/addresses/<address>/sends": queries.get_sends_by_address,
        "/v2/addresses/<address>/receives": queries.get_receive_by_address,
        "/v2/addresses/<address>/sends/<asset>": queries.get_sends_by_address_and_asset,
        "/v2/addresses/<address>/receives/<asset>": queries.get_receive_by_address_and_asset,
        "/v2/addresses/<address>/dispensers": queries.get_dispensers_by_address,
        "/v2/addresses/<address>/dispensers/<asset>": queries.get_dispenser_by_address_and_asset,
        "/v2/addresses/<address>/dispenses/sends": queries.get_dispenses_by_source,
        "/v2/addresses/<address>/dispenses/receives": queries.get_dispenses_by_destination,
        "/v2/addresses/<address>/dispenses/sends/<asset>": queries.get_dispenses_by_source_and_asset,
        "/v2/addresses/<address>/dispenses/receives/<asset>": queries.get_dispenses_by_destination_and_asset,
        "/v2/addresses/<address>/sweeps": queries.get_sweeps_by_address,
        "/v2/addresses/<address>/issuances": queries.get_issuances_by_address,
        "/v2/addresses/<address>/assets": queries.get_valid_assets_by_issuer,
        "/v2/addresses/<address>/transactions": queries.get_transactions_by_address,
        "/v2/addresses/<address>/dividends": queries.get_dividends_distributed_by_address,
        "/v2/addresses/<address>/orders": queries.get_orders_by_address,
        "/v2/addresses/<address>/fairminters": queries.get_fairminters_by_address,
        "/v2/addresses/<address>/fairmints": queries.get_fairmints_by_address,
        "/v2/addresses/<address>/fairmints/<asset>": queries.get_fairmints_by_address_and_asset,
        ### /addresses/<address>/compose/ ###
        "/v2/addresses/<address>/compose/bet": transaction.compose_bet,
        "/v2/addresses/<address>/compose/broadcast": transaction.compose_broadcast,
        "/v2/addresses/<address>/compose/btcpay": transaction.compose_btcpay,
        "/v2/addresses/<address>/compose/burn": transaction.compose_burn,
        "/v2/addresses/<address>/compose/cancel": transaction.compose_cancel,
        "/v2/addresses/<address>/compose/destroy": transaction.compose_destroy,
        "/v2/addresses/<address>/compose/dispenser": transaction.compose_dispenser,
        "/v2/addresses/<address>/compose/dividend": transaction.compose_dividend,
        "/v2/addresses/<address>/compose/issuance": transaction.compose_issuance,
        "/v2/addresses/<address>/compose/mpma": transaction.compose_mpma,
        "/v2/addresses/<address>/compose/order": transaction.compose_order,
        "/v2/addresses/<address>/compose/send": transaction.compose_send,
        "/v2/addresses/<address>/compose/sweep": transaction.compose_sweep,
        "/v2/addresses/<address>/compose/dispense": transaction.compose_dispense,
        "/v2/addresses/<address>/compose/fairminter": transaction.compose_fairminter,
        "/v2/addresses/<address>/compose/fairmint": transaction.compose_fairmint,
        "/v2/addresses/<address>/compose/attach": transaction.compose_attach,
        "/v2/utxos/<utxo>/compose/detach": transaction.compose_detach,
        "/v2/utxos/<utxo>/compose/movetoutxo": transaction.compose_movetoutxo,
        ### /assets ###
        "/v2/assets": queries.get_valid_assets,
        "/v2/assets/<asset>": queries.get_asset,
        "/v2/assets/<asset>/balances": queries.get_asset_balances,
        "/v2/assets/<asset>/balances/<address>": queries.get_balance_by_address_and_asset,
        "/v2/assets/<asset>/orders": queries.get_orders_by_asset,
        "/v2/assets/<asset>/matches": queries.get_order_matches_by_asset,
        "/v2/assets/<asset>/credits": queries.get_credits_by_asset,
        "/v2/assets/<asset>/debits": queries.get_debits_by_asset,
        "/v2/assets/<asset>/dividends": queries.get_dividends_by_asset,
        "/v2/assets/<asset>/issuances": queries.get_issuances_by_asset,
        "/v2/assets/<asset>/sends": queries.get_sends_by_asset,
        "/v2/assets/<asset>/dispensers": queries.get_dispensers_by_asset,
        "/v2/assets/<asset>/dispensers/<address>": queries.get_dispenser_by_address_and_asset,
        "/v2/assets/<asset>/holders": queries.get_asset_holders,
        "/v2/assets/<asset>/dispenses": queries.get_dispenses_by_asset,
        "/v2/assets/<asset>/subassets": queries.get_subassets_by_asset,
        "/v2/assets/<asset>/fairminters": queries.get_fairminters_by_asset,
        "/v2/assets/<asset>/fairmints": queries.get_fairmints_by_asset,
        "/v2/assets/<asset>/fairmints/<address>": queries.get_fairmints_by_address_and_asset,
        ### /orders ###
        "/v2/orders": queries.get_orders,
        "/v2/orders/<order_hash>": queries.get_order,
        "/v2/orders/<order_hash>/matches": queries.get_order_matches_by_order,
        "/v2/orders/<order_hash>/btcpays": queries.get_btcpays_by_order,
        "/v2/orders/<asset1>/<asset2>": queries.get_orders_by_two_assets,
        "/v2/orders/<asset1>/<asset2>/matches": queries.get_order_matches_by_two_assets,
        "/v2/order_matches": queries.get_all_order_matches,
        ### /bets ###
        "/v2/bets": queries.get_bets,
        "/v2/bets/<bet_hash>": queries.get_bet,
        "/v2/bets/<bet_hash>/matches": queries.get_bet_matches_by_bet,
        "/v2/bets/<bet_hash>/resolutions": queries.get_resolutions_by_bet,
        ### /burns ###
        "/v2/burns": queries.get_all_burns,
        ### /dispensers ###
        "/v2/dispensers": queries.get_dispensers,
        "/v2/dispensers/<dispenser_hash>": queries.get_dispenser_info_by_hash,
        "/v2/dispensers/<dispenser_hash>/dispenses": queries.get_dispenses_by_dispenser,
        ### /dividends ###
        "/v2/dividends": queries.get_dividends,
        "/v2/dividends/<dividend_hash>": queries.get_dividend,
        "/v2/dividends/<dividend_hash>/credits": queries.get_dividend_disribution,
        ### /events ###
        "/v2/events": queries.get_all_events,
        "/v2/events/<int:event_index>": queries.get_event_by_index,
        "/v2/events/counts": queries.get_all_events_counts,
        "/v2/events/<event>": queries.get_events_by_name,
        "/v2/events/<event>/count": queries.get_event_count,
        ### /dispenses ###
        "/v2/dispenses": queries.get_dispenses,
        ### /sends ###
        "/v2/sends": queries.get_sends,
        ### /issuances ###
        "/v2/issuances": queries.get_issuances,
        "/v2/issuances/<tx_hash>": queries.get_issuance_by_transaction_hash,
        ### /sweeps ###
        "/v2/sweeps": queries.get_sweeps,
        "/v2/sweeps/<tx_hash>": queries.get_sweep_by_transaction_hash,
        ### /broadcasts ###
        "/v2/broadcasts": queries.get_valid_broadcasts,
        "/v2/broadcasts/<tx_hash>": queries.get_broadcast_by_transaction_hash,
        ### /fairminters ###
        "/v2/fairminters": queries.get_all_fairminters,
        "/v2/fairminters/<tx_hash>": queries.get_fairminter,
        "/v2/fairminters/<tx_hash>/mints": queries.get_fairmints_by_fairminter,
        ### /bitcoin ###
        "/v2/bitcoin/addresses/utxos": addrindexrs.get_unspent_txouts_by_addresses,
        "/v2/bitcoin/addresses/<address>/transactions": addrindexrs.get_transactions_by_address,
        "/v2/bitcoin/addresses/<address>/transactions/oldest": util.get_oldest_transaction_by_address,
        "/v2/bitcoin/addresses/<address>/utxos": addrindexrs.get_unspent_txouts,
        "/v2/bitcoin/addresses/<address>/pubkey": util.pubkeyhash_to_pubkey,
        "/v2/bitcoin/transactions/<tx_hash>": util.get_transaction,
        "/v2/bitcoin/estimatesmartfee": bitcoind.fee_per_kb,
        "/v2/bitcoin/transactions": bitcoind.sendrawtransaction,
        ### /mempool ###
        "/v2/mempool/events": queries.get_all_mempool_events,
        "/v2/mempool/events/<event>": queries.get_mempool_events_by_name,
        "/v2/mempool/transactions/<tx_hash>/events": queries.get_mempool_events_by_tx_hash,
        ### /healthz ###
        "/v2/healthz": util.check_server_health,
        "/healthz": util.check_server_health,
        ### API v1 ###
        "/": util.redirect_to_rpc_v1,
        "/v1/": util.redirect_to_rpc_v1,
        "/api/": util.redirect_to_rpc_v1,
        "/rpc/": util.redirect_to_rpc_v1,
    }
)
