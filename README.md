> **This branch is a private security-audit share, not the canonical Counterparty Core README.**
> The original project README is preserved at [`README.upstream.md`](README.upstream.md).
> Companion doc: [`AUDIT_TRACKING.md`](AUDIT_TRACKING.md) — chronological investigation log.

# Halt, Accounting & Fund-Theft Audit — Branch Notes

**Branch:** `fix/parse-time-exception-handling` (based on `master`)
**Sessions:** 2026-04-22 → 2026-04-23 (kept local, not pushed)
**Scope:** Targeted security audit for (a) consensus-halt vectors in parse/validate paths, (b) accounting errors — wrong credits/debits, unauthorized ownership transfers, mint-supply-for-less-than-price, (c) message-dispatch gaps (send variants, version gates), (d) reorg/rollback hygiene, (e) API surface (DoS / data exposure), (f) Rust indexer panic surface.

This document records **what was fixed**, **why it mattered** (with concrete attack traces where relevant), and **what was ruled out** (so the same ground does not get re-audited). For the gritty details on any single change, the commit message has it.

---

## TL;DR

- **1 CRITICAL fund-theft bug** in `btcpay.parse` — anyone can swipe a pending BTC order match's escrow. 11 years latent on mainnet (verified, 2,158/2,158 historical btcpays were honest). Fix gated, tested, ready.
- **~14 single-tx halt vectors** mostly post-`taproot_support` (CBOR-crafted message bodies). All gated/fixed, none weaponized.
- **6+ already-deployed quiet bugs** in API drift / state DB derivation. Empirical mainnet verification quantifies each (e.g. 1,517/1,517 sweeps have `valid=NULL`).
- **8 Rust panic surfaces** in the indexer hot path. All fixed, `cargo check` clean.
- **0 real accounting drifts** on mainnet ledger DB across 10 years and 20M messages — the strongest possible empirical confirmation that the credit/debit machinery is internally consistent.
- **4 protocol-gated future fixes** with `999999999` placeholder activation blocks pending coordination.

90 commits ahead of `master`, all local. Run `git log master..HEAD --oneline` for the full list.

---

## 🚨 CRITICAL — btcpay anyone-can-steal-escrow ([`2057e099c`](https://github.com/droplister/counterparty-core/commit/2057e099c))

**Pre-fix vulnerability.** When two orders match (one BTC side, one asset side), the asset is escrowed and the BTC-owing party has ~10 blocks to send BTC to the asset-escrowing party in a tx with the `order_match_id` in OP_RETURN. `btcpay.parse` checked `tx["btc_amount"] >= btc_quantity` but **never compared `tx["destination"]` to the legitimate counterparty's address**. Combined with `check_btcpay_source` (active mainnet ≥ block 313900) bypassing the source check, **any third party could**:

1. Watch the chain for pending order_matches
2. Craft a Bitcoin tx: vout 0 = their own address with at least `btc_quantity` BTC, vout 1 = OP_RETURN with the `order_match_id`
3. `parse()` credits them with the escrowed asset and marks the match completed
4. The legitimate BTC-owing party's later honest tx hits "order match completed" → invalid; the asset-escrowing party never receives their BTC

Net cost to attacker: just the Bitcoin tx fee (BTC paid to themselves comes back). Net gain: the entire escrowed asset.

**Empirical verification (gcloud kubectl read-only SQL on prod ledger DB):** 2,158 historical post-block-313900 mainnet btcpays, 100% from the legitimate BTC-owing party with destination = legitimate counterparty. **0 historical exploitation in 11 years** — the bug has been latent.

**Fix:** `btcpay.parse` now rejects when `tx["destination"] != destination` (the value validated from order_match), gated behind new `check_btcpay_destination` protocol entry. Pre-fix path preserved for historical consensus determinism. Two regression tests pin both behaviors:

- `test_btcpay_attacker_diverts_with_gate_OFF_legacy_behavior` → asserts gate-off preserves the buggy behavior (Carol gets credited)
- `test_btcpay_attacker_diverts_with_gate_ON_rejects` → asserts gate-on rejects with `status=invalid: btc payment destination does not match order match counterparty`

Both pass. The fix waits for activation block coordination.

**Recommended urgency:** the bug is now documented in this local branch. Release window matters; recommend rapid coordinated release with activation soon after (10-100 blocks lead time).

---

## Halt vectors fixed (single attacker tx → chain stops)

| Commit | File | Attack |
|---|---|---|
| [`fb7fe8288`](https://github.com/droplister/counterparty-core/commit/fb7fe8288) | `broadcast.py` parse() | CBOR `[None, 0.0, 0, "text/plain", b""]` decodes cleanly via `load_cbor`; `min(None, MAX_INT)` raises `TypeError` → halt. Wrap post-unpack `min()`/`validate()` in `try/except (TypeError, ValueError, OverflowError, AssertionError)`. |
| [`46ae1089b`](https://github.com/droplister/counterparty-core/commit/46ae1089b) | `issuance.py` unpack() + parse() | Three vectors: `asset_id < 26**3` raised `TypeError` when CBOR supplied `asset_id` as `str`; `validate()` tuple-unpack raised `ValueError` on CBOR edge cases; CBOR-huge ints overflowed SQLite signed-64-bit `INTEGER` on `insert_record`. |
| [`46ae1089b`](https://github.com/droplister/counterparty-core/commit/46ae1089b) | `broadcast.py` unpack() | `VarIntSerializer.SerializationTruncationError` from attacker-truncated rawtext was uncaught. Added broad `except Exception`. |
| [`caf578242`](https://github.com/droplister/counterparty-core/commit/caf578242) | `cancel.py` parse() | `UnboundLocalError` on `offer_type` when unpack raised before assignment. Init `offer_type = None`; emit `INVALID_CANCEL` event on failure path. |
| [`caf578242`](https://github.com/droplister/counterparty-core/commit/caf578242) | `destroy.py` parse() | CBOR-huge `quantity` passed validate but INSERT raised `OverflowError`. Added defensive clamp before bindings. |
| [`99012352f`](https://github.com/droplister/counterparty-core/commit/99012352f) | `utxo.py` parse() | UTF8-invalid decrypted body raised uncaught `UnicodeDecodeError`. Wrapped `unpack` in `try/except UnpackError`. |
| [`2375e6bc4`](https://github.com/droplister/counterparty-core/commit/2375e6bc4), [`0247a4ce2`](https://github.com/droplister/counterparty-core/commit/0247a4ce2) | `utxo.py` parse() | Polish on the invalid-record path (added `source` field, renamed event to `INVALID_UTXO_MOVE`). |
| [`3880dcac3`](https://github.com/droplister/counterparty-core/commit/3880dcac3), [`4b902ff7f`](https://github.com/droplister/counterparty-core/commit/4b902ff7f) | `dispense.py` parse() | `NoPriceError` from `get_must_give` and downstream `ZeroDivisionError` could propagate → halt. Wrapped in `try/except: continue` over the dispenser loop. |
| [`32c5c731f`](https://github.com/droplister/counterparty-core/commit/32c5c731f) | `issuance.py` unpack() | Added `struct.error` to outer except tuple. |
| [`01e7d50e8`](https://github.com/droplister/counterparty-core/commit/01e7d50e8) | `order.py`, `bet.py` match() | `assert len(orders) == 1` / `assert len(bets) == 1` are 10+ years old and never fired, but any future DB inconsistency would halt. Replaced with log+return. |
| [`c624cc96b`](https://github.com/droplister/counterparty-core/commit/c624cc96b) | `dispense.py` `get_must_give` | **Notable.** Attacker creates an oracle dispenser pointing at their own address, broadcasts value=-1 (`broadcast.validate` doesn't reject negatives, the row is inserted before the negative-value early-return), then any BTC payment to the dispenser yields negative `must_give` → `credit(quantity=-N)` → `CreditError` → halt. Widened guard to `if last_price <= 0: raise NoPriceError`. **Empirical:** 0 oracle dispensers historically pointed at any of the 3,335 negative-value broadcasters; bug real but never weaponized. |
| [`0aefc5794`](https://github.com/droplister/counterparty-core/commit/0aefc5794) | `fairminter.py:99,564` | Multi-dot subasset longname (e.g. `"PARENT.foo.bar"`) is valid per `validate_subasset_longname` (allows non-consecutive dots). `existing_asset["asset_longname"].split(".")` then unpacks 3 parts to 2 vars → `ValueError` → halt. Use `split(".", 1)`. **Empirical:** 1,636 multi-dot subasset longnames exist; 0 fairminters opened on any of them. |
| [`0aefc5794`](https://github.com/droplister/counterparty-core/commit/0aefc5794) | `utils/assetnames.py` `expand_subasset_longname` | 100KB compacted CBOR longname → ~25s of O(n²) string-concat + integer-divide CPU per tx. Cap input at 200 bytes (a 250-char base68 longname needs 191 bytes; legacy struct path is uint8-bounded; only CBOR is uncapped). **Empirical:** max mainnet longname is 249 chars; cap is provably safe historically. |
| [`49a754098`](https://github.com/droplister/counterparty-core/commit/49a754098) | `attach.py:188` | Python truthiness bug: `if op_return_output and ...` short-circuits when `op_return_output == 0`, so OP_RETURN at vout 0 + attach to vout 0 silently bypassed the OP_RETURN check → asset attached to unspendable OP_RETURN, permanently locked. Fix: `is not None`. Gated behind `fix_attach_op_return_check` (consensus-affecting). **Empirical:** 0 historical mainnet occurrences. |
| [`598d2c680`](https://github.com/droplister/counterparty-core/commit/598d2c680) | `parser/follow.py` `receive_rawblock` | Malformed rawblock / transient telemetry/RPC error during catch_up killed the entire BlockchainWatcher (block + mempool ingestion). Wrap with try/except that re-raises only `ParseTransactionError` (true halt) and logs+continues on operational exceptions. |
| [`598d2c680`](https://github.com/droplister/counterparty-core/commit/598d2c680) | `parser/follow.py` `is_late()` | RPC errors from `getblockcount()` propagated to handle()'s broad except → `self.stop()`. Same auth-flap class as the prior BackendHeight retry-spam fix. Treat RPC errors as "not late." |
| [`598d2c680`](https://github.com/droplister/counterparty-core/commit/598d2c680) | `parser/follow.py` late_since logic | `if self.is_late() and late_since is None: late_since = time.time()` reset itself on every iteration, so the 60s catch_up trigger could never fire. Fix the condition. |
| [`a40bc44f8`](https://github.com/droplister/counterparty-core/commit/a40bc44f8) | `parser/mempool.py` `parse_mempool_transactions` | A halt-class tx broadcast to mempool propagated `ParseTransactionError` → killed the watcher. Single attacker tx in mempool = all confirmation processing dies. Wrap with `try/except` that drops the speculative batch (already rolled back by the `with db:` context). Also moved `set_parsing_mempool(False)` to a `finally` to prevent stuck-singleton state. |
| [`14403373c`](https://github.com/droplister/counterparty-core/commit/14403373c) | `parser/check.py` `software_version()` | `check_change` reads upstream JSON values and compares them with `<` to ints. A compromised counterparty.io / DNS-poisoned response delivering a string where an int was expected raises `TypeError` not in the existing except tuple → propagates through BlockchainWatcher.handle() → self.stop(). Caught by adding `(KeyError, TypeError, AttributeError)` to the except tuple. |

---

## Rust panic surface fixed (`counterparty-rs`)

| Commit | File | Issue |
|---|---|---|
| [`b207f8b68`](https://github.com/droplister/counterparty-core/commit/b207f8b68) | `indexer/bitcoin_client.rs` | `parse_vout` had off-by-one bounds checks at two sites (P2PKH-ish and multisig). `bytes[1..=prefix.len()]` needs `bytes.len() > prefix.len()`, not `>=`. Also added `data_len` clamp + early-return when `data_len < prefix.len()`. |
| [`ff082f9e1`](https://github.com/droplister/counterparty-core/commit/ff082f9e1) | `utils.rs` | `script_to_address_legacy` had `panic!("we thought this shouldn't happen!")` on attacker-bytes else-branch. Replaced with `PyErr::new::<PyValueError, _>(...)`. |
| [`7566d70ea`](https://github.com/droplister/counterparty-core/commit/7566d70ea) + [`183c71af6`](https://github.com/droplister/counterparty-core/commit/183c71af6) | `indexer/bitcoin_client.rs` | `BATCH_CLIENT.lock().unwrap()` panicked the worker on a poisoned mutex (any prior panic-while-holding cascaded to all subsequent workers). `BatchRpcClient::new(...).unwrap()` panicked on bad rpc_address config. Use `unwrap_or_else(|p| p.into_inner())` for poison-recovery and clone the client out before dropping the guard so we don't hold the mutex across network IO (perf regression). |
| [`97e0d662b`](https://github.com/droplister/counterparty-core/commit/97e0d662b) | `indexer/database.rs:146` | `.expect()` on a missing `BlockAtHeightHasHash` index entry crashed the worker on dirty-shutdown DB state. Convert to `Error::Database` so the worker error-channel handles it. |
| [`9ff88ac8c`](https://github.com/droplister/counterparty-core/commit/9ff88ac8c) | 4 sites in `indexer/handlers/start.rs`, `indexer/utils.rs`, `indexer/workers/{writer,reporter}.rs` | u32 underflow patterns: `start_height - 1` when `start_height == 0`; `target_height - reorg_window` when `target_height < 50` (small chains); `height - reorg_window`; `prev_height` and `CP_HEIGHT` math in reporter. Use `saturating_sub` and additive forms. |

---

## API drift / quiet accounting bugs (already-deployed)

These are bugs that have been silently affecting mainnet for some time. Most are derived state (state DB), not consensus state, but they cause snapshot-bootstrapped vs event-streamed nodes to diverge or API consumers to silently miss data.

| Commit | File | Issue | Empirical |
|---|---|---|---|
| [`f3b01b532`](https://github.com/droplister/counterparty-core/commit/f3b01b532) | `api/migrations/0004` | `LEFT JOIN` had a `WHERE` on the right table → silently became INNER JOIN. Assets issued but never destroyed were missing from supplies. Removed WHERE. | — |
| [`12bfd2f9e`](https://github.com/droplister/counterparty-core/commit/12bfd2f9e) | `messages/sweep.py` | Every other parse module calls `set_transaction_status(...)`; sweep didn't. Every sweep on mainnet has `valid=NULL` in `transactions_status`. API filters using `valid=1`/`valid=0` silently exclude all sweeps. Invalid sweeps were entirely invisible. Added the call + invalid-record persistence. | **1,517/1,517 (100%)** of mainnet sweeps affected. Verifiable via `api.counterparty.io:4000/v2/transactions/<sweep_tx_hash>` → `"valid": null`. |
| [`fac268916`](https://github.com/droplister/counterparty-core/commit/fac268916) | `api/apiwatcher.py:57` | `EVENTS_ADDRESS_FIELDS["DETACH_FROM_UTXO"] = ["sourc_address", "destination"]` — typo. Source-side address_events silently dropped for every DETACH. | All DETACH events affected. |
| [`ef7903a3d`](https://github.com/droplister/counterparty-core/commit/ef7903a3d) | `api/apiwatcher.py` | `update_assets_info` never set `description_locked` (mig 0004 reads it from issuances, streamed handler doesn't). Snapshot-bootstrapped node has `description_locked=1`; streamed node has `0`. Same shape for `xcp_supply` (no `status='valid'` filter). Both fixed. | — |
| [`d40892da1`](https://github.com/droplister/counterparty-core/commit/d40892da1) | new `0014.fix_assets_info_latest_issuance_columns.py` | Migration 0004 selected `description`/`divisible`/`mime_type`/`owner` via bare-column SELECT alongside MIN/MAX aggregates → SQLite picks bare columns "from one of" the min/max rows, implementation-dependent. Snapshot vs streamed nodes drifted for re-issued/transferred assets. New corrective migration re-derives from latest valid issuance. | — |
| [`d8e46d349`](https://github.com/droplister/counterparty-core/commit/d8e46d349) | new `0015.fix_assets_info_locked_int_drift.py` | Migration 0004 wrote `SUM(locked)` and `SUM(description_locked)` into `BOOL DEFAULT 0` columns → snapshot-bootstrapped node had `locked=3` for assets with three locking issuances; streamed node had `locked=1`. Both truthy but unequal. Re-derive as `MAX(...) ∈ {0,1}`. | — |

(Migrations 0014/0015 follow the existing 0006 ATTACH-without-DETACH pattern: query `pragma_database_list` to attach idempotently, never DETACH while yoyo's write tx is open. See commits [`78b5e6915`](https://github.com/droplister/counterparty-core/commit/78b5e6915) and [`79d964da1`](https://github.com/droplister/counterparty-core/commit/79d964da1).)

---

## Operational hygiene & concurrency

| Commit | What |
|---|---|
| [`e66953cd1`](https://github.com/droplister/counterparty-core/commit/e66953cd1) | `BackendHeight.refresh()` assigned `last_check` only on success path. Persistent 401/403/502 RPC errors drove ~10 Hz retries (~36k RPC calls/hr). Move assignment to `finally`. |
| [`aa968b8b6`](https://github.com/droplister/counterparty-core/commit/aa968b8b6) | `transactions_status` orphan rows on reorg. Added to `clean_transactions_tables` + rebuild_database drop list. |
| [`64c0ab4f5`](https://github.com/droplister/counterparty-core/commit/64c0ab4f5) + [`24a0e24fb`](https://github.com/droplister/counterparty-core/commit/24a0e24fb) | `rollback()` didn't clear `backend.bitcoind.TRANSACTIONS_CACHE`. Added `reset_caches()` helper + call. |
| [`25ddfe5a6`](https://github.com/droplister/counterparty-core/commit/25ddfe5a6) | Reorg + mempool hygiene batch: `clean_mempool` only walked the events table (mempool_transactions leak); `rollback()` didn't truncate mempool/mempool_transactions (post-reorg state stale); `handle_reorg` walked unbounded past genesis on wrong-network/corrupt-DB; `reparse()` didn't clear bitcoind caches. All four addressed. |
| `148829885` | ZMQ `connect_to_zmq` reconnect leaked sockets+context (long-running indexers eventually exhausted fds). Plus `RCVTIMEO` typo (set on wrong socket). Both fixed. |
| [`e1d666a45`](https://github.com/droplister/counterparty-core/commit/e1d666a45) | `UTXOLocks` (composer.py) singleton was shared across werkzeug worker threads with no synchronization. Two concurrent compose calls between `filter_unspent_list` and `lock_inputs` could pick the same UTXO. Added `threading.Lock`. |
| [`2f5452967`](https://github.com/droplister/counterparty-core/commit/2f5452967) | block_index forwarding pattern: 6 gated `unpack(message)` callsites + `dividend.unpack` were dropping `tx["block_index"]`, falling back to `CurrentState`. During real-time parse these match, but it's a footgun for any future caller that re-parses with stale CurrentState. Pass the explicit block_index. |
| [`204178df7`](https://github.com/droplister/counterparty-core/commit/204178df7) | Decimal context leak: `helpers.divide` and `verbose.normalize_price` set `decimal.getcontext().prec = N` as a side effect, mutating the thread-local Decimal context permanently. If `helpers.divide` is ever imported into a parse path, every subsequent Decimal op on that thread silently uses prec=16 → consensus-split footgun. Switched to `with decimal.localcontext()`. |
| [`24869bdea`](https://github.com/droplister/counterparty-core/commit/24869bdea) + [`75944d5ef`](https://github.com/droplister/counterparty-core/commit/75944d5ef) | `SingletonMeta` had a classic check-then-act race; two threads could both pass the `not in _instances` check before either stored. Added class-level `threading.Lock` with double-checked locking. Also fixed `reset_caches()` not clearing `@functools.lru_cache` wrappers (`getrawtransaction`, `get_utxo_address_and_value`) — orphaned UTXO data persisted across reorg. Guarded with `hasattr` for test fixtures that monkey-patch. |
| [`6205cf91a`](https://github.com/droplister/counterparty-core/commit/6205cf91a) | `gas.py:112` libm cross-platform threshold inline comment. ULP drift in `math.exp(-k * (t - midpoint))` is absorbed by `int(fee * UNIT)` at current `base_fee=1`; if `base_fee` ever exceeds ~1.8e8 the drift becomes visible across libm implementations. Documented at the call site. |

---

## API security

| Commit | What |
|---|---|
| [`6f3a73c55`](https://github.com/droplister/counterparty-core/commit/6f3a73c55) | `apiv1.py:281` `filter_["field"]` was f-string interpolated into SQL with no validation. Body like `filters=[{"field":"1) UNION SELECT password,1,1 FROM ...--",...}]` reads any column the API process can see. Apply `^[a-z0-9_]+$` regex (matches existing `order_by` validation). |
| [`6f3a73c55`](https://github.com/droplister/counterparty-core/commit/6f3a73c55) | `cli/server.py` debug-logged the entire config dict including `BACKEND_PASSWORD`, `RPC_PASSWORD`, `API_PASSWORD`, `BACKEND_COOKIE`. Operators with `--verbose` or Sentry breadcrumbs leaked credentials. Redact any key matching `PASSWORD/SECRET/COOKIE/TOKEN/KEY`. |
| [`50ef4e9ac`](https://github.com/droplister/counterparty-core/commit/50ef4e9ac) | `--api-only` shutdown loop never checked `is_set()`; `stop()` set the event but the loop kept running. Added the check. |
| [`74d7ba380`](https://github.com/droplister/counterparty-core/commit/74d7ba380) | `apiv1.py:565` `sql` JSON-RPC method (added by PhantomPhreak in 2014, undocumented in `apiary.apib`, live + unauthenticated on `api.counterparty.io:4000` for 12 years). DB is read-only so no exfil risk (Counterparty data is public), but is a real DoS surface (`randomblob(1e9)`, recursive CTEs, N-way self-joins on the 20M-row messages table). Smallest behavior change: require `RPC_PASSWORD` to be set for the method to work. Operators who want it opt in by setting a password. |

---

## Protocol-gated future fixes (pending activation coordination)

These are consensus-affecting fixes that need a coordinated activation block. All default OFF (placeholder `block_index=999999999`, signet `0` for testing). Adam's call on activation timing.

| Gate | Fix | Risk if delayed |
|---|---|---|
| `check_btcpay_destination` | Reject btcpay txs whose destination doesn't match the legitimate counterparty (the CRITICAL above) | **Active fund-theft window** until shipped. 11 years latent + 2,158/2,158 honest history → low recent risk, but documented in this branch now → tickling the noise floor. |
| `fix_sort_bet_matches` | Bet matching currently sorts in tx_index order due to a `sorted(...) result discarded` no-op (10+ years old). Gated proper sort by price-then-tx_index. | Bet matching suboptimality, no fund safety. |
| `canonical_subasset_compact` | Reject CBOR subasset issuances whose `compact(expand(bytes)) != bytes` — fixes leading-zero pad and "phantom `!`" malleability. | Asset-name malleability; no historical reach (4,065 post-taproot subasset issuances, all canonical via compose). |
| `fix_attach_op_return_check` | Use `is not None` instead of Python truthiness in the OP_RETURN check (asset-loss bug for attach to OP_RETURN at vout 0). | Asset self-loss only; 0 historical mainnet occurrence. |

---

## Investigated and closed as not-a-bug

- **F2 `safe_get_utxo_address` "unknown" sentinel** — agent claim was that detach.py would credit assets to a phantom address called `"unknown"` when `utxo_address` couldn't be derived from a non-standard scriptPubKey (bare multisig, P2PK). Empirical investigation traced the 5 historical "unknown" cases on mainnet: 4 are `utxo move` (asset moves to a real new UTXO with non-derivable address — recoverable by spending), 1 is legacy message-type-100 utxo.py which defaults `recipient` to "first non-OP_RETURN output" (not to `balance["utxo_address"]`). All 5 have `address=NULL, utxo=<real_utxo>, utxo_address="unknown"` — the assets are spendable; "unknown" is purely metadata noise on the column. The theoretical detach.py path that WOULD produce a phantom-address credit has 0 mainnet occurrences. **No fix; documented for future reference.**
- **Concurrency dict-cache races** (`TRANSACTIONS_CACHE` / `BLOCKS_CACHE` racing with reorg) — reads are atomic dict-key access (GIL); the only race is "entry added by `add_transaction_in_cache` between its two non-atomic ops survives a concurrent `clear()`" which is benign cache state. Skipped per "don't add complexity for theoretical issues" pattern.
- **APIv1 `sql` JSON-RPC endpoint** as data exfil — turned out to be intentional 2014 feature by PhantomPhreak (commit [`a29759ee8`](https://github.com/droplister/counterparty-core/commit/a29759ee8)). DB is read-only; Counterparty data is public; no exfil risk. Closed the DoS-surface concern with [`74d7ba380`](https://github.com/droplister/counterparty-core/commit/74d7ba380) (require auth).
- **Several wave-3 agent claims** that didn't reproduce empirically (concurrency SingletonMeta/cache scenarios, address-pack/unpack edge cases, etc.) — discarded after verification.

---

## Verified-safe (don't re-audit without new info)

### Authorization sweep (across all 21 message handlers) — fully clean
Every credit/debit/transfer/ownership-change verified tied to `tx["source"]` or to immutable record fields populated at original signer-authorized insert time. **0 wrongful-credit paths found.** Empirically validated against mainnet: every (address, asset) balance equals exactly SUM(credits) − SUM(debits) across 10 years of history.

### Per-message audit results
- **Fairmint / Fairminter:** supply-for-less-than-price not exploitable (`math.ceil` on payment always favors protocol); premint claim by non-contributor not possible (premint always credited to stored `fairminter["source"]`); hard-cap overshoot prevented by strict `>` check; parameter constraints enforced at open time; premint escrow/unescrow balanced across all state transitions.
- **Send family:** cross-version dispatch correctly gated; credit/debit symmetry holds; self-send double-credit not possible; source authorization correct; mpma partial-success guarded by status check.
- **Sweep / Dividend / Dispenser:** sweep source authorization gated by `tx["source"]`; sweep doesn't touch escrow funds (orders/bets/dispensers escrow outside `balances`); dividend rounding net-zero by construction; dispenser create/refill/close all auth-gated; dispenser escrow atomicity correct on rollback.
- **Gas / fee:** libm non-determinism absorbed at current `base_fee=1`; integer overflow bounded; counter manipulation not possible (gated inside `status==valid`); reorg rollback correct.
- **Bet/order settlement math:** CFD rounding can create/burn 1 sat — but CFDs disabled at block 312350 (effectively dead). Other settlement math conserved by construction.
- **Address pack/unpack:** all reachable failure modes caught; Rust paths return `PyResult` (no panic surface beyond what we already fixed).
- **DB migrations 0001-0015:** systematic audit against the LEFT JOIN bug class. Two HIGH findings in 0004 fixed via 0014/0015; the rest verified clean.
- **Composer paths:** no ledger mutation in compose (verified by exhaustive grep). UTXO selection has the singleton-thread-safety issue we fixed.

### General
- **ConsensusHashBuilder** singleton is sound; hash order stable.
- **Reorg detection** in `handle_reorg` is correct; rollback table list complete after our fixes; cache invalidation correct after our fixes.
- **`mainnet_burns.csv`** content pre-audited by user (out of scope).
- **`protocol_changes.json`** structure verified; 106 entries, no anomalous block indexes; value-only entries correctly never used as boolean gates.

---

## Empirical mainnet validation

Connected to GKE `public-mainnet` cluster, pod `counterparty-0`, `/data/counterparty.db`, **driver-level read-only** (`?mode=ro`) via gcloud + kubectl. Ran a series of invariant + scope queries.

### Strongest invariant: balance == credits − debits (per address, asset)

| Query | Result |
|---|---|
| `COUNT(*) FROM balances WHERE quantity < 0` | **0** — no negative balances anywhere |
| XCP per-address: balance vs SUM(credits)−SUM(debits) (exact INT) | **0 drift** |
| All-assets per-(address, asset), REAL math | 183 "drifts" max 16640 sat |
| Per-asset INTEGER spot-check on EDRACHMA (highest "drift") | **0 actual drift** — Q3a's 183 was double-precision ulp loss on high-supply early assets (MAIDSAFE/EDRACHMA/etc.) |

**The mainnet ledger DB has 0 real accounting drifts** across ~10 years and ~20M messages. Every (address, asset) balance equals exactly the sum of its credits minus its debits.

### Bug-exposure scope (all of these had 0 historical exploitation)

| Bug | Population | Exploited? |
|---|---|---|
| btcpay destination check | 2,158 historical post-313900 mainnet btcpays | **0 (100% honest)** |
| Sweep `transactions_status` | 1,517 sweeps | **1,517 affected (metadata; no fund loss)** |
| Dispenser oracle halt | 3,335 negative-value broadcasts × 727 open oracle dispensers | 0 cross-matches |
| Fairminter multi-dot halt | 1,636 multi-dot subasset longnames | 0 fairminters opened on any |
| Subasset 200-byte cap | 4,065 post-taproot subasset issuances | 0 over 200 bytes (max 249-char longname → ~191 bytes) |
| Attach OP_RETURN at vout 0 | (0 historical occurrences) | 0 |

### Other counts (for context)

| Metric | Value |
|---|---|
| Total messages (events journal) | 20,082,170 |
| Total blocks | 667,985 |
| Total assets | 248,464 |
| Addresses with non-zero balance | 401,768 |
| Open BTC-side orders | 106,060 |
| Open dispensers | 284,806 |
| Largest single-asset supply | 9,223,372,036,854,775,807 (= MAX_INT — confirmed someone issued at the boundary) |

---

## Static analysis

Semgrep p/security-audit + p/python + p/owasp-top-ten + p/cwe-top-25 against `lib/`. **4 total warnings**, all already suppressed with `nosec`/`noqa` markers, all justified (signed snapshot downloads, intentional rw-group config files). Codebase is clean from Semgrep's perspective.

CodeQL deferred (CLI not installed in this environment; given Semgrep clean and extensive multi-agent + manual coverage, lower-leverage than the work we did).

---

## Fuzz infrastructure (uncommitted, local-only per directive)

`fuzz_parse_test.py` + `fuzz_cbor_test.py` exist in `counterpartycore/test/units/messages/` as untracked files. 19 Hypothesis-based fuzz tests + CBOR-encoded adversarial-tuple tests across issuance, subasset, broadcast, enhancedsend, sweep, dispense, destroy, cancel, utxo, send, bet, order, fairmint, fairminter, dispenser parse paths.

Run via `hatch run pytest counterpartycore/test/units/messages/fuzz_*.py -x`. Recommended to formalize and commit as part of CI if desired.

---

## Pending / open

- **Activation block coordination** for the 4 protocol-gated fixes (esp. `check_btcpay_destination`).
- **Hash-compare against an independent reference node** — the strongest possible end-to-end check; user environment.
- **Regtest scenario sweep** against this branch — user environment.
- **Subasset canonicalization scan** (Python: decrypt + decode 4,065 CBOR messages, check round-trip) before activating `canonical_subasset_compact`. Almost certainly clean (compose always produces canonical), but verifying programmatically would let the activation block be set freely.
- **CodeQL deeper interprocedural pass** if wanted.
- **APIv1 `sql` endpoint** decision: deprecate, document on v2, or leave as opt-in-via-RPC_PASSWORD.

---

## Reviewing this branch

```bash
git log master..HEAD --oneline           # 90 commits
git diff master..HEAD --stat             # files changed summary
git show <commit-hash>                    # any specific fix
```

Each commit message includes: the attack vector, the fix mechanism, and (where relevant) the reasoning for choosing one fix pattern over alternatives. This document and those commit messages together should be enough to pick this branch up cold. The companion `AUDIT_TRACKING.md` has the chronological "running notes" of how each finding was investigated.

For the headline finding, `git show 2057e099c` has the full btcpay attack model + fix + test rationale.
