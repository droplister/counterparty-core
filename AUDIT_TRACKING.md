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
| 10 | JSON-RPC API surface | **DONE — 3 fixes** | Fixed `6f3a73c55`: APIv1 SQL injection via `filter_["field"]` (HIGH data exfil), credentials redaction in server.py debug log (HIGH ops). Fixed `50ef4e9ac`: `--api-only` shutdown loop honors stop event (MEDIUM). Open: APIv1 `sql` JSON-RPC DoS (HIGH ops, recommend deprecation, depends on operator config). |
| 11 | Rate limiter, threading, logging | DONE (with #10) | Same audit pass. Server thread orchestration verified; rate limiting is per-IP via Cloud Armor (out of scope). |
| 12 | dispatch.py + threading | **DONE — clean** | Same audit pass. Dispatch table verified clean: every ID maps to a known handler with the correct protocol gate; no gaps allow misroute. |

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
| 4 | Property-based fuzz | **EXPANDED (uncommitted)** | `fuzz_cbor_test.py` extended to cover broadcast (would have caught bug_009), enhancedsend, sweep. Still local-only per directive. **Future:** compose path fuzz, address pack/unpack fuzz. |
| 5 | Cross-platform consensus (libm, float, locale, encoding) | **DONE — no critical** | Sweep covered all `math.*`, `float()`, locale, struct endianness, random/hash. F1 (`mpmaencoding.py:171` `math.log2(num_addresses)`) is defense-in-depth — realistically benign today (small ulp drift always absorbed by ceil at small inputs). F6 (gas.py Decimal ** D(1.5)) — current code already safe after Decimal context leak fix. |
| 6 | State integrity per table | **DONE — 2 fixes + 2 design notes** | Systematic per-table sweep done. Fixed `12bfd2f9e`: sweep.parse missing set_transaction_status (every other parse module had it; sweeps' `valid` column was permanently NULL). Fixed `d8e46d349` + `0dc4e2505`: assets_info.locked SUM-as-int drift (snapshot N-issuances yielded `locked: 3`, streamed yielded `locked: 1`). Open design notes: `addresses` table lacks tx_hash (joinability); `transactions_status` lacks block_index (cleanup is post-hoc but works). Both LOW. |
| 7 | Compose ↔ parse symmetry | **DONE — 1 fix** | Composer audit + protocol gates audit covered this. Fixed `5e1ba61a0`: `btc_order_minimum` mirrored into `order.validate` so honest users get ComposeError instead of broadcasting an invalid tx and burning BTC fee. Other asymmetries (fairminter end_block, bet deadline) are LOW (compose more conservative, no fund-loss). |
| 8 | Protocol gates audit | **DONE — 1 fix + 2 LOW deferred** | Fixed `5e1ba61a0` btc_order_minimum mirror. Open LOW: pervasive `unpack(message)` calls in parse drop `tx["block_index"]` (latent footgun if API ever re-parses historical tx); `dispenser.py:388` uses `CurrentState` instead of tx block. Both safe today via implicit equality. |
| 9 | Authorization predicates | **DONE — clean** | Systematic sweep across all 21 message handlers verified every credit/debit/transfer/ownership-change is tied to `tx["source"]` or to immutable record fields populated at original signer-authorized insert time. No new vulnerabilities. |
| 10 | SQL invariants on snapshots | USER TASK | `SUM(credits) - SUM(debits)` per asset, no negative balances, no orphaned rows. Needs mainnet snapshot. |
| 11 | Static analysis (Semgrep / CodeQL) | **DONE — Semgrep clean; CodeQL deferred (install)** | Ran p/security-audit + p/python + p/owasp-top-ten + p/cwe-top-25 against `lib/`. 4 total warnings, all already suppressed with `nosec`/`noqa`. CodeQL CLI not installed in this environment (multi-hundred-MB download); given Semgrep is clean and we've done extensive manual + multi-agent adversarial review, CodeQL is lower-leverage than running fix-review on the branch. CodeQL can be added later if a deeper interprocedural-taint pass is wanted. |
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
- 2026-04-22 20:30 — Group-1 batch (6 of 6): ZMQ socket leak + RCVTIMEO typo (`148829885`), UTXOLocks intra-worker thread safety (`e1d666a45`), block_index forwarding to all gated unpack callsites (`2f5452967`). One agent claim found incorrect (events_count/transaction_types_count already in MIGRATIONS_AFTER_ROLLBACK, no action needed).
- (next) Wave 4 — audit unaudited areas: subasset compaction encoder, electrs.py, parser/check.py, Rust worker/handler plumbing.
