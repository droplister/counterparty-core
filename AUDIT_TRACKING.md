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
| 4 | Mempool / unconfirmed handling | TODO | `lib/parser/mempool.py`. Race conditions with block parse, cache consistency on rollback. |
| 5 | Address pack/unpack | **DONE — no critical** | F1 (`unpack_legacy` raises uncaught TypeError on `b"\x80"`) is caught by outer parse_tx broad except → not a halt. F2 (legacy returns garbage base58 → XCP burn) is consensus-locked behavior, attacker burns own funds. F3 (`setup_bitcoinutils` global) is a code smell. Rust paths verified safe (PyResult, no panic). |
| 6 | Asset name generation (subasset, numeric, longname) | TODO | `assetnames.py` or equivalent. Collision risk between subasset longname compaction and numeric IDs. |
| 7 | Composer paths | TODO | `api/composer.py`, `messages/*/compose`. Compose errors raise `ComposeError` (caught by API), so probably not consensus. Verify no compose path mutates ledger state before raising. |
| 8 | State DB rebuild logic | TODO | `rebuild_database` re-derives state. Could be inconsistent with fresh-sync node. We touched the cleanup table list already. |
| 9 | ZMQ / parser/follow.py | TODO | Connection drops, message reordering, Bitcoin Core reorgs faster than indexer can keep up. |
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
| 11 | Static analysis (Semgrep / CodeQL) | TODO | Both available via skills. Quick win. |
| 12 | Adversarial multi-agent review | DONE multiple rounds | Ultrareview + 12+ ad-hoc agents. ~50% FP rate on ad-hoc, ~0% FP on ultrareview. |

---

## Active triage (open findings, awaiting decision)

Things found but not yet acted on. As we resolve them, move to AUDIT_NOTES.md.

| Finding | File | Severity | Decision |
|---|---|---|---|
| 0004 indeterminate bare columns for `description`/`divisible`/`mime_type` | `api/migrations/0004.create_and_populate_assets_info.py:71-96` | HIGH (API drift) | Fix? Mirrors existing `issuer` subquery pattern. |
| 0004 `owner` from arbitrary issuance row | same file:82 | HIGH (API drift) | Fix? Use `ORDER BY rowid DESC LIMIT 1` subquery. |
| `sort_bet_matches` no-op | `messages/bet.py:424-428` | MEDIUM | Cannot fix without consensus break. Document only. |
| CFD settlement rounding (CRITICAL in principle, dead in practice) | `messages/broadcast.py:397-400` | LOW (effectively dead) | Document. Pre-312350 dead code path. |
| BATCH_CLIENT mutex `.unwrap()` poisoning + config-time `.unwrap()` | `counterparty-rs/src/indexer/bitcoin_client.rs:754-765` | MEDIUM | Fix? Idiomatic Rust improvement. |
| Decimal context leak via raw `getcontext().prec` | (already done — `204178df7`) | DONE | — |

---

## Running notes (append as you find things)

- 2026-04-22 17:50 — Dispenser negative-oracle-price halt verified and fixed (`c624cc96b`). Found via wave-1 dispenser audit agent. Required cross-checking `broadcast.parse` insertion order, `get_oracle_last_price` query, `dispense.get_must_give` math, and `ledger.events.credit` guards.
- 2026-04-22 17:50 — Wave 1 of 4 parallel agents (areas 1, 2, 3, 5) all returned. Two real fixes (dispenser halt, audited migrations needs decision); two areas verified safe (address, send already done).
- (next) Wave 2 candidates: areas 4 (mempool), 6 (asset names), 7 (composer), 8 (rebuild), 9 (follow.py).
