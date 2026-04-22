# Audit Tracking

Companion to `AUDIT_NOTES.md`. **AUDIT_NOTES.md is the record of what we did.** This file is the **plan of what's left** — checkboxes per area + per methodology angle, plus a running log of new issues found mid-stream so we don't lose them as N+1 things surface.

Update inline as items move done/skipped/pending.

---

## List 1 — Codebase areas

Areas of the codebase to audit (or confirm-safe) for halt vectors, accounting bugs, and authorization gaps. Order is rough priority-by-yield.

| # | Area | Status | Notes |
|---|---|---|---|
| 1 | DB migrations 0001-0008+ | **DONE — 2 HIGH findings** | `0004` `description`/`divisible`/`mime_type` from indeterminate min/max bare-column row; `owner` populated from arbitrary issuance row instead of latest. Snapshot-bootstrapped nodes drift from event-streamed nodes for re-issued/transferred assets. **Not consensus, but API serves wrong data.** Pending fix decision. |
| 2 | Bet/order settlement math | **DONE — 1 dead-CRITICAL + 1 MEDIUM** | CFD rounding can create/burn 1 sat — but CFDs disabled at block 312350, no pending pre-312350 matches → effectively dead. `sort_bet_matches` at bet.py:424-428 calls `sorted()` and discards the result → bet matches in tx_index order, not best-price-first. Cannot fix without protocol break. Document only. |
| 3 | Dispenser dispense math + oracle | **DONE — 1 CRITICAL FIXED** | Negative oracle price → negative must_give → CreditError → ParseTransactionError → halt. Fixed in `c624cc96b`. Float arithmetic in oracle math (Finding 1) is consensus-fragile but not currently exploitable. Multi-dispenser-per-output is intentional post-`multiple_dispenses`. STATUS_CLOSING grace-period dispense is intentional. |
| 4 | Mempool / unconfirmed handling | **DONE — 1 CRITICAL FIXED + 3 open** | Fixed `a40bc44f8`: ParseTransactionError in mempool path no longer takes down watcher; `set_parsing_mempool(False)` now in finally. Open: mempool_transactions leak (HIGH), reorg leaves stale mempool events (HIGH), RawMempoolParser thread connection (LOW). |
| 5 | Address pack/unpack | **DONE — no critical** | F1 (`unpack_legacy` raises uncaught TypeError on `b"\x80"`) is caught by outer parse_tx broad except → not a halt. F2 (legacy returns garbage base58 → XCP burn) is consensus-locked behavior, attacker burns own funds. F3 (`setup_bitcoinutils` global) is a code smell. Rust paths verified safe (PyResult, no panic). |
| 6 | Asset name generation (subasset, numeric, longname) | **DONE — 2 HIGH FIXED + 2 open** | Fixed `0aefc5794`: fairminter `split(".")` halt on multi-dot longname; expand_subasset_longname CBOR DoS (~25s/tx for 100KB payload). Open: F-1 non-injective expand modulo 0 (MEDIUM, ledger-state-benign), F-4 generate_asset_name(26**12) non-round-trippable (LOW). |
| 7 | Composer paths | **DONE — no critical** | No ledger mutation in compose ✓. UTXOLocks not thread-safe (MEDIUM, multi-worker concern). Skip-validation bypass in several composers (LOW UX). All compose-stricter-than-parse drift items LOW. |
| 8 | State DB rebuild logic | **DONE — 1 HIGH FIXED + 3 open** | Fixed `fac268916`: DETACH typo. Open: H2 description_locked sync (HIGH), M1 events_count/transaction_types_count rollback (MEDIUM), M2 xcp_supply status check (MEDIUM). |
| 9 | ZMQ / parser/follow.py | **DONE — 3 fixes + several open** | Fixed `598d2c680`: late_since dead-code, is_late RPC error tear-down, receive_rawblock no try/except. Open: reorg horizon unbounded (HIGH), connect_to_zmq socket leaks (MEDIUM), reparse missing reset_caches (LOW). |
| 10 | JSON-RPC API surface | TODO | `apiv1.py`, `api/v2/*`. Defensive only — API errors don't affect consensus. |
| 11 | Rate limiter, threading, logging | TODO | Operational hygiene. Not correctness. |
| 12 | dispatch.py + threading | PARTIAL | Send dispatch already verified clean. dispatch.py message-ID table not separately checked. |

**Bonus (not in original 12):**
- RPS / RPSResolve / Burn — DONE, all clean (RPS is replay stub).
- Fairmint / Fairminter — DONE, all clean.
- Sweep + Dividend — DONE, all clean.
- Send variants (send1, enhancedsend, mpma) — DONE, all clean.
- Gas.py — DONE, libm comment added; latent issues if base_fee bumped.
- Rust indexer broader audit — DONE, only LOW/MED mutex-poisoning items.

---

## List 2 — Methodology angles (fresh-start systematic analysis)

How I'd approach this codebase if I had no prior context. Order matters: top-down funnel.

| # | Angle | Status | Notes |
|---|---|---|---|
| 1 | Threat model / trust boundaries | DONE (implicit) | Captured in AUDIT_NOTES "Attack Model". |
| 2 | Halt-mechanism map | DONE | `parser/blocks.py:parse_tx` → `ParseTransactionError` → halt. Every `messages/*/parse()` is a leaf. |
| 3 | Catalog exception classes per leaf | DONE per leaf | Hardened against `(struct.error, TypeError, ValueError, OverflowError, AssertionError, ZeroDivisionError, KeyError, UnpackError)` across all message types audited. |
| 4 | Property-based fuzz | PARTIAL | `fuzz_parse_test.py` + `fuzz_cbor_test.py` exist (uncommitted, local-only). Cover issuance + subasset CBOR + 19 byte-fuzz targets. **TODO:** extend to broadcast CBOR (would have caught bug_009), compose, address pack/unpack, sweep/enhancedsend/mpma CBOR. |
| 5 | Cross-platform consensus (libm, float, locale, encoding) | PARTIAL | gas.py audited, libm threshold documented in code. **TODO:** sweep for all `math.*`, `float()`, locale-dependent operations across ledger/messages. |
| 6 | State integrity per table | PARTIAL | Hit `transactions_status` orphans on reorg, `0004` LEFT JOIN. **TODO:** systematic per-table writer/reader/rollback inventory for every table in the schema. |
| 7 | Compose ↔ parse symmetry | TODO | For each compose+parse pair: does parse accept exactly what compose can emit? More? Less? Compose-only validations not enforced by parse are attack surface. |
| 8 | Protocol gates audit | TODO | Every `protocol.enabled()` check across compose, validate, parse, cross-message. A gate present in compose but missing in parse means attacker uses feature pre-activation. |
| 9 | Authorization predicates | PARTIAL | Per-message audited (sweep, dispenser, send, fairmint). **TODO:** systematic sweep across every credit/debit/transfer/ownership-change for source verification. |
| 10 | SQL invariants on snapshots | USER TASK | `SUM(credits) - SUM(debits)` per asset, no negative balances, no orphaned rows. Needs mainnet snapshot. |
| 11 | Static analysis (Semgrep / CodeQL) | **DONE — Semgrep clean** | Ran p/security-audit + p/python + p/owasp-top-ten + p/cwe-top-25 against `lib/`. 4 total warnings (2x dynamic-urllib in `cli/bootstrap.py`, 2x insecure-file-permissions `os.chmod(0o660)` in `bootstrap.py` + `setup.py`) — all already suppressed with `nosec`/`noqa` markers, all justified (signed snapshot downloads, intentional rw-group config files). CodeQL still TODO. |
| 12 | Adversarial multi-agent review | DONE multiple rounds | Ultrareview + 12+ ad-hoc agents. ~50% FP rate on ad-hoc, ~0% FP on ultrareview. |

---

## Active triage (open findings, awaiting decision)

Things found but not yet acted on. As we resolve them, move to AUDIT_NOTES.md.

| Finding | File | Severity | Decision |
|---|---|---|---|
| 0004 indeterminate bare columns for `description`/`divisible`/`mime_type` | `api/migrations/0004.create_and_populate_assets_info.py:71-96` | HIGH (API drift) | New migration to re-derive (vs editing 0004 retroactively). |
| 0004 `owner` from arbitrary issuance row | same file:82 | HIGH (API drift) | Same — new migration. |
| description_locked never updated by streamed handler | `api/apiwatcher.py:321-338` | HIGH (API drift) | Add `description_locked` to `set_data` mirroring `locked` pattern. |
| events_count / transaction_types_count over-count on rollback | `api/apiwatcher.py:380, 391-409` + `MIGRATIONS_AFTER_ROLLBACK` | MEDIUM | Add 0005 to `MIGRATIONS_AFTER_ROLLBACK` or implement decrement. |
| update_xcp_supply ignores `status='valid'` filter | `api/apiwatcher.py:270-284` | MEDIUM | Add status check before UPDATE. |
| mempool_transactions cleanup leak (no-event txs never cleaned) | `parser/mempool.py:153-169` | HIGH | Iterate `mempool_transactions` table in `clean_mempool` too. |
| Reorg leaves stale mempool events referencing rolled-back chain | `parser/blocks.py:587-606` + `follow.py:144-147` | HIGH | Truncate `mempool` and `mempool_transactions` on rollback. |
| Reorg horizon unbounded in handle_reorg | `parser/blocks.py:730-750` | HIGH | Assert `previous_block_index >= BLOCK_FIRST` before loop. |
| connect_to_zmq leaks sockets on reconnect | `parser/follow.py:239-243` | MEDIUM | Close old sockets/context before creating new. |
| reparse missing reset_caches | `parser/blocks.py:633-643` | LOW | Add `backend.bitcoind.reset_caches()` after reparse. |
| BATCH_CLIENT mutex `.unwrap()` poisoning + config-time `.unwrap()` | `counterparty-rs/src/indexer/bitcoin_client.rs:754-765` | MEDIUM | Idiomatic Rust improvement. |
| UTXOLocks not thread/process-safe | `api/composer.py:559` + `utils/helpers.py:51` | MEDIUM (UX) | Wrap in threading.Lock or move to shared store. |
| expand_subasset_longname F-1 non-injective (modulo 0) | `utils/assetnames.py:204` | MEDIUM (state-benign) | Reject `% 68 == 0` or use 1-indexed remainder. Probably needs protocol gate. |
| generate_asset_name(26**12) non-round-trippable | `ledger/issuances.py:65-91` | LOW | Tighten loop boundary; protocol-gate. |
| `sort_bet_matches` no-op | `messages/bet.py:424-428` | DONE | Fixed in `fbc3e8e71` behind `fix_sort_bet_matches` gate. |
| CFD settlement rounding (CRITICAL in principle, dead in practice) | `messages/broadcast.py:397-400` | LOW (effectively dead) | Document only. Pre-312350 dead code path. |
| Decimal context leak via raw `getcontext().prec` | DONE | `204178df7` | — |

---

## Running notes (append as you find things)

- 2026-04-22 17:50 — Dispenser negative-oracle-price halt verified and fixed (`c624cc96b`). Found via wave-1 dispenser audit agent. Required cross-checking `broadcast.parse` insertion order, `get_oracle_last_price` query, `dispense.get_must_give` math, and `ledger.events.credit` guards.
- 2026-04-22 17:50 — Wave 1 of 4 parallel agents (areas 1, 2, 3, 5) all returned. Two real fixes (dispenser halt, audited migrations needs decision); two areas verified safe (address, send already done).
- 2026-04-22 18:30 — Wave 2 of 5 parallel agents (areas 4, 6, 7, 8, 9) all returned. Six halt/critical fixes shipped: bet sort gated fix, DETACH typo, BlockchainWatcher hardening (3 issues), mempool tear-down, asset-name multi-dot halt, asset-name CBOR DoS. New protocol entry `fix_sort_bet_matches` added to protocol_changes.json (placeholder activation 950000 mainnet).
- 2026-04-22 18:30 — User pattern confirmed: when a fix requires consensus break, gate it behind a new `protocol_changes.json` entry rather than skip. Applied for `fix_sort_bet_matches`. Pre-fix path preserved for historical determinism.
- 2026-04-22 19:30 — Triage batch shipped (6 commits): apiwatcher description_locked sync + xcp_supply status filter (`ef7903a3d`), mempool/reorg hygiene (`25ddfe5a6`: leak fix + truncate-on-rollback + horizon bound + reparse cache reset), Rust BATCH_CLIENT mutex poison-recovery (`7566d70ea`), corrective migration 0014 for assets_info latest-issuance drift (`d40892da1`).
- 2026-04-22 19:40 — Semgrep launched in background. Will triage findings on completion, then continue list.
- (next) Process Semgrep results, then return to remaining list items (#10 API, #11 ops, #12 dispatch) + methodology angles (#4 fuzz, #5 libm, #8 protocol gates).
