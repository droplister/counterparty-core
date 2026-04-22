# Halt & Accounting Audit — Session Notes

**Branch:** `fix/parse-time-exception-handling` (based on `master`)
**Session:** 2026-04-22 (ongoing — kept local, not pushed)
**Scope:** Targeted security audit for (a) consensus-halt vectors in parse/validate paths, (b) accounting errors — wrong credits/debits, unauthorized ownership transfers, mint-supply-for-less-than-price, (c) message-dispatch gaps (send variants, version gates), (d) reorg/rollback hygiene.

This document records **what was fixed**, **why it mattered** (with concrete attack traces where relevant), and **what was ruled out** (so the same ground does not get re-audited).

---

## Attack Model

1. An attacker can craft arbitrary transaction bytes carrying any Counterparty message body.
2. With `taproot_support` active (mainnet block ≥ 902000), the message body may be CBOR-encoded — CBOR can carry `None`, `float`, `str`, or arbitrary-size ints where the legacy `struct` format would have rejected the bytes.
3. Any uncaught exception inside a message-type `parse()` propagates up through `parser/blocks.py:parse_tx`, which wraps it as `ParseTransactionError` and re-raises from `parse_block` — **this halts every node on the network at the offending block.**
4. `validate()` rejections (returning `problems`) are fine: they mark the record invalid and the chain continues.

Goal of this branch: turn every attacker-triggered exception in a parse path into an *invalid-record* outcome rather than a consensus halt, without changing the legitimate-traffic behavior.

---

## Commits on this branch (20 direct fixes + 18 from branch-point on master, all local)

Ordered newest → oldest within each category. `master` is the base.

### Halt vectors fixed

| Commit | File | Attack |
|---|---|---|
| `fb7fe8288` | `broadcast.py` parse() | CBOR `[None, 0.0, 0, "text/plain", b""]` decodes cleanly via `load_cbor`; `min(None, MAX_INT)` raises `TypeError` → halt. Fix: wrap post-unpack `min()`/`validate()` in `try/except (TypeError, ValueError, OverflowError, AssertionError)`. Pattern copied from `issuance.parse` 46ae108. Found by ultrareview (bug_009). |
| `46ae1089b` | `issuance.py` unpack() + parse() | Three halt vectors surfaced by hypothesis-based CBOR fuzzing. (1) `asset_id < 26**3` raised `TypeError` when CBOR supplied `asset_id` as `str`. Widened outer `unpack` except to include `(TypeError, ValueError, OverflowError)`. (2) Tuple-unpack of `validate()`'s return raised `ValueError` on CBOR edge cases. Wrapped `validate()` call in `parse()` in `try/except (TypeError, ValueError, OverflowError, AssertionError)`. (3) CBOR-huge ints overflowed SQLite 64-bit signed INTEGER on `insert_record`. Added defensive `_clamp` loop over all int bindings before INSERT. |
| `46ae1089b` | `broadcast.py` unpack() | `VarIntSerializer.SerializationTruncationError` from attacker-truncated rawtext was not caught; existing excepts only covered `struct.error` and `AssertionError`. Added broad `except Exception` after the specific ones. |
| `caf578242` | `cancel.py` parse() | `UnboundLocalError` on `offer_type` when unpack raised before assignment. Attack: hand-rolled cancel whose encoded offer_hash decoded but offer lookup raised downstream. Initialized `offer_type = None` at top of function; on failure path emit event `INVALID_CANCEL`. |
| `caf578242` | `destroy.py` parse() | CBOR-huge `quantity` passed validate (validate caps at MAX_INT but only rejects via `problems`), then INSERT bound `quantity` via SQLite, which rejected > 2^63−1 as `OverflowError`. Added `safe_quantity = None if quantity > MAX_INT else quantity` clamp before bindings. |
| `99012352f` | `utxo.py` parse() | Arbitrary-UTF8-invalid decrypted body raised uncaught `UnicodeDecodeError`/`ValueError` inside `unpack`. Wrapped `unpack` call in `try/except UnpackError`; on failure, insert an invalid `sends` record and set tx status False. |
| `2375e6bc4` | `utxo.py` parse() | Follow-up: invalid-record bindings were missing `source` field. Added `"source": tx["source"]`. |
| `0247a4ce2` | `utxo.py` parse() | Polish: failure path hardcoded event `"ATTACH_TO_UTXO"` even when original intent was detach. Renamed to `INVALID_UTXO_MOVE` (mirrors `INVALID_CANCEL` precedent). Found by ultrareview (bug_010). No consensus impact. |
| `3880dcac3` | `dispense.py` parse() | `NoPriceError` from `get_must_give` when dispenser state transiently zero-priced would propagate → halt. Wrapped in `try/except NoPriceError: continue` over the dispenser loop. |
| `4b902ff7f` | `dispense.py` parse() | Also catch `ZeroDivisionError` in `get_must_give` call site for defense-in-depth. |
| `32c5c731f` | `issuance.py` unpack() | `struct.error` from malformed legacy bytes was only partly covered (outer except caught `UnpackError` only). Added `struct.error` to the tuple. |
| `01e7d50e8` | `order.py`, `bet.py` match() | `assert len(orders) == 1` and `assert len(bets) == 1` were 10+ years old and had never fired, but any future DB inconsistency would halt the chain. Replaced with `if len(...) != 1: logger.error; return`. Softening, not removal — still logs the invariant violation. |

### Rollback / reorg hygiene

| Commit | File | Issue |
|---|---|---|
| `aa968b8b6` | `parser/blocks.py` | `transactions_status` rows were not cleaned by `clean_transactions_tables` on reorg. If the new chain is shorter, orphan rows from the longer chain remained. Added to the cleanup list, and to `rebuild_database` drop list. |
| `64c0ab4f5` | `parser/blocks.py` | `rollback()` did not clear `backend.bitcoind.TRANSACTIONS_CACHE`. Stale deserialized txs could be served after rollback. Added `backend.bitcoind.reset_caches()` call. |
| `24a0e24fb` | `backend/bitcoind.py` | Encapsulated the cache clear: new `reset_caches()` helper so callers don't poke at module-level dicts directly. |

### Operational hygiene

| Commit | File | Issue |
|---|---|---|
| `e66953cd1` | `ledger/backendheight.py` | `refresh()` assigned `self.last_check` only as its last statement, so a failing RPC call short-circuited before the assignment. Persistent 401/403/502/unlisted-RPC-error cases drove ~10 Hz retries (~36k RPC calls/hr + ~36k log lines/hr). Fix: moved assignment into a `finally` block so the invariant holds regardless of exception path. Found by ultrareview (bug_002). No consensus impact. |

### API / data-integrity

| Commit | File | Issue |
|---|---|---|
| `f3b01b532` | `api/migrations/0004.create_and_populate_assets_info.py` | The supplies query had `WHERE issuances_quantity.asset = destructions_quantity.asset` on a `LEFT JOIN`, which silently converted it back to an INNER JOIN. Assets that had been issued but never destroyed were then missing from the supplies view. Removed the WHERE clause. |

### Rust (counterparty-rs)

| Commit | File | Issue |
|---|---|---|
| `b207f8b68` | `indexer/bitcoin_client.rs` | `parse_vout` had off-by-one bounds checks at two sites (~line 200 P2PKH-ish path, ~line 314 multisig path). Inclusive slice `bytes[1..=prefix.len()]` needs `bytes.len() > prefix.len()`, not `>=`. Also added `data_len = min(bytes[0] as usize, bytes.len() - 1)` clamp and early-return when `data_len < prefix.len()` so `data[prefix.len()..]` cannot panic. `cargo check` clean. |
| `ff082f9e1` | `utils.rs` | `script_to_address_legacy` had `panic!("we thought this shouldn't happen!")` in the else branch of non-witness address parsing. Attacker bytes reaching that branch would PyPanic → process crash. Replaced with `PyErr::new::<PyValueError, _>(...)`. |

### Latent-landmine / footgun cleanup

| Commit | File | Issue |
|---|---|---|
| `204178df7` | `utils/helpers.py`, `api/verbose.py` | `helpers.divide` and `verbose.normalize_price` set `decimal.getcontext().prec = N` as a side effect, mutating the thread-local Decimal context permanently. Current call graph only invokes these from the API thread, so the parser thread (gas.py, dividend.py, fairmint.py Decimal math) keeps the default prec=28 — no current exploit. But any future caller that imports `helpers.divide` into a parse path would silently drop thread precision to 16 for the rest of that thread's life — a consensus-split footgun. Switched both to `with decimal.localcontext() as ctx: ctx.prec = N:` so precision is scoped. Found by gas.py audit pass. |

### Defensive dead-code reverts (prior turns, included for completeness)

| Commit | File | Note |
|---|---|---|
| `4c811f06f` | `dispense.py` | Reverted a defensive `give_remaining < 0` soft error back to the original `assert give_remaining >= 0`. After trace, the "negative" condition was unreachable given the validate path; the softening added dead code. |

---

## Ultrareview findings (external pass, 2026-04-22)

4 findings, all verified real (no false positives this pass). 3 applied; 1 deferred.

| Bug | Severity | Status |
|---|---|---|
| bug_009 — `broadcast.parse` CBOR halt | consensus halt | Fixed in `fb7fe8288` |
| bug_002 — `BackendHeight` retry spam | operational | Fixed in `e66953cd1` |
| bug_010 — `utxo.parse` hardcoded event name | nit | Fixed in `0247a4ce2` |
| bug_005 — CI workflow unpinned nightly Rust | nit | Deferred. In upstream commit `88896bc87 fix electrs installation on github` (Ouziel, 2026-01-30). Out of halt/accounting audit scope; reverting may break whatever drove the nightly switch. |

---

## Verified-safe (don't re-audit without new info)

### Fairmint / Fairminter (user's explicit concern)
- **Supply-for-less-than-price.** `fairmint.py:66-69, 205-206` uses `math.ceil` on the attacker's XCP payment. Traced every off-grid `quantity` — every deviation from `quantity = N * quantity_by_price` yields *fewer* units per sat, not more. `compose` enforces grid alignment, but even without it parse-time favors the protocol. No exploit.
- **Premint claim by non-contributor.** Premint always credited to `fairminter["source"]` (stored at open time from `tx["source"]`). No user-controllable path to forge the `source` stored in `fairminters` row.
- **Hard-cap overshoot.** Cap check uses strict `>` in validate and `==` for close; `partial_mint_to_reach_hard_cap` clamps `earn_quantity = hard_cap - asset_supply`. Commission split preserves total: `earn_quantity + commission = original_quantity`.
- **Fairminter parameter constraints.** `max_mint_per_tx <= max_mint_per_address`, `quantity_by_price >= 1`, `hard_cap % quantity_by_price == 0`, and MAX_INT bounds all enforced at open time.
- **Premint escrow/unescrow symmetry.** Balanced across open, soft-cap-reached, soft-cap-missed, hard-cap-close state transitions.

### Send family (user flagged many versions + if/else gap concern)
- **Cross-version routing.** `blocks.py:165-174` dispatch gates each version by both `message_type_id` AND `protocol.enabled(...)`. No bypass — IDs are disjoint constants (0, 2, 3).
- **Credit without debit / vice versa.** All three paths (send1, enhancedsend, mpma) guard debit+credit under `if status == "valid"` with debit before credit, inside the outer transaction in `blocks.py:131`. Any raise between debit and credit rolls back.
- **Self-send double-credit.** Debit runs before credit on the same SQLite row; SQLite serializes intra-tx ops. Net zero minus fees.
- **Source authorization.** All three use `tx["source"]` for debit, never trust message-embedded source.
- **MPMA partial success.** `status == "valid"` guards the whole for-loop body; no partial ledger writes.
- **Defensive gaps not fixed (not currently reachable):**
  - `send1.validate:62-65` calls `active_options(result["options"], ...)` which would do `None & int → TypeError` if `options` were NULL. Schema allows NULL (`options INTEGER` no NOT NULL), but no current INSERT path writes NULL. Latent. Skipped per "no defensive dead code" policy.
  - `mpma.validate:76-83` lacks a `return problems` after `isinstance(quantity, int)` check. Unreachable today because `_decode_mpma_send_decode` uses `uintbe:64` bitstream reads that always yield int. Latent.
  - `ledger/events.py:250` `assert asset == config.XCP` when `len(address) == 40`. No concrete byte sequence produces a 40-char address (base58 ~34, bech32 ~42). Theoretical.
  - `sweep.py` CBOR path: `flags=-1` passes `flags > FLAGS_ALL` (7) check and `(-1) & FLAGS_ALL == 7` is truthy. Behaves identically to `flags=7` on-chain, stored as `-1` in `sweeps` table. No value impact, consensus stable. Cosmetic.

### Sweep / Dividend / Dispenser (user's explicit concern: unauthorized ownership transfer, wrong credits)
- **Sweep source authorization.** `tx["source"]` drives balance debit (`sweep.py:227-235`) and issuance ownership check (`last_issuance["issuer"] == tx["source"]` at line 263). Attacker cannot sweep an address they didn't sign from.
- **Sweep dangling references.** Open orders/bets/dispensers escrow funds outside the `balances` table. Sweep only moves free balances; escrow rows resolve to original source on refund/close.
- **Dividend rounding.** Each `dividend_quantity = int(address_quantity * quantity_per_unit / UNIT)`; `dividend_total = sum(dividend_quantity)`. Debit matches sum-of-credits by construction.
- **Dividend holder selection.** `exclude_empty`, `no_dividend_to_self`, `dispensers_in_holders` all protocol-gated and consensus-stable.
- **Dispenser authorization.** Create can only come from an address with balance of the asset (`validate` 85-90). Refill/close require `tx["source"] == action_address` OR `tx["source"] == existing["origin"]` (lines 526-529, 616-629). `give_quantity`/`satoshirate` immutable after creation (lines 522-524).
- **Dispenser escrow atomicity.** Empty-address debit/credit/debit wrapped in `if is_empty_address:`; on exception, `DebitError` caught and DB tx rolls back. No half-escrow.

### Gas / fee (user flagged as relatively new code)
- **libm non-determinism at current params.** `int(math.exp(x) * base_fee)` at `base_fee=1` absorbs ULP drift via `int()` floor. ULP-perturbation scan across `x∈[4,14]`: zero fee delta. Derived drift bound: consensus breaks only if `base_fee ≥ ~1.8e8` (8 orders above current).
- **Integer overflow.** Worst-case fee (`(x−b)^1.5 / 100 * UNIT` at attacker-maximized x≈15873 tx/block) ≈ 2e12 sats, 6 orders below int64 cap.
- **Counter manipulation.** `increment_counter` gated inside `status == "valid"` + `action == "attach to utxo"` in utxo.parse and attach.parse — symmetric with fee debit.
- **NaN/Inf halt.** `calculate_fee` inputs come from DB-derived ints and protocol-specified `fee_parameters` — neither attacker-controllable. `math.exp` arg bounded at mainnet params (max ≈ 6), far below 709 overflow threshold.
- **Reorg rollback.** `transaction_count` in `TABLES` list (blocks.py:91); `DELETE FROM transaction_count WHERE block_index >= ?` runs in rollback. Correct.

### General
- **ConsensusHashBuilder determinism.** Singleton construction is sound; hash order is stable.
- **API v2 limit check / SQL injection.** Prior concern dismissed after finding it is allowlist-protected.
- **BLOCKS_CACHE.** Appears unused in the hot path — dead code, not a correctness risk.

---

## Fuzz infrastructure (uncommitted, local-only)

Per user directive: **do not commit, run local**. These files exist as untracked files in `counterparty-core/counterpartycore/test/units/messages/`:

- `fuzz_parse_test.py` — 19 Hypothesis-based fuzz tests, 100 examples each (~1,900 random byte inputs). Targets: issuance, broadcast, dispense, destroy, cancel, utxo, send, bet, order, sweep, fairmint, fairminter, dispenser parse paths.
- `fuzz_cbor_test.py` — CBOR-encoded tuples with adversarial types (None, str, float, bool, binary, ints up to 2^64). Targets: issuance + subasset parse. **Should extend to broadcast** — that's where bug_009 lives; a broadcast fuzz would have caught it pre-ultrareview.

Run via hatch:
```
hatch run pytest counterpartycore/test/units/messages/fuzz_parse_test.py -x
hatch run pytest counterpartycore/test/units/messages/fuzz_cbor_test.py -x
```

Known test artifact: `apsw.ConstraintError` from repeated `tx_hash` across Hypothesis examples. Caught explicitly and skipped in the fuzz bodies — not a real bug.

---

## Pending / open

- **Property-based fuzzing extensions not yet added:** broadcast CBOR fuzz (would have caught bug_009 locally), compose fuzz, address pack/unpack fuzz, sweep/enhancedsend/mpma CBOR fuzz.
- **Static analysis** (CodeQL, Semgrep) — user requested but not yet run.
- **Regtest scenario sweep** against this branch — user's environment; instructions in memory (`reference_wsl_regtest.md`).
- **Integration validation** — fresh-sync hash comparison against a reference node on mainnet. User's task.
- **Latent gas.py issues (LOW, not currently exploitable):**
  - If `base_fee` in `fee_parameters` is ever bumped above ~1.8e8, switch `D(math.exp(x))` to `(x).exp()` under a fixed-precision `localcontext` to eliminate libm cross-platform drift.
  - Dead branch in `get_transaction_count_for_last_period` (gas.py:56-58) — `fetchone()` on `SELECT SUM(...)` never returns None. Polish only.
- **Still not audited** (candidates for a future pass): `rps.py`/`rpsresolve.py` (rock-paper-scissors — deprecated but still in parse path?), `burn.py` (initial XCP distribution, probably frozen), additional Rust indexer code beyond `parse_vout`, JSON-RPC / API surface, p2p / mempool handling.

---

## Reviewing this branch

```bash
git log master..HEAD --oneline
git diff master..HEAD --stat
```

To verify a specific fix:
```bash
git show <commit-hash>
```

Each commit message includes: the attack vector, the fix mechanism, and (where relevant) the reasoning for choosing one fix pattern over alternatives. This document and those commit messages together should be enough to pick this branch up cold.
