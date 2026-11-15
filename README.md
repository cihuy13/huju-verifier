# huju-verifier

Verification gates for an on-chain agent. This repository documents the
checking discipline behind **huju.ai**, an autonomous agent operating on
BNB Smart Chain, and ships one of its gates as a standalone tool:
`verifier.py`, a read-only checker that refuses to call a result real
until two independent RPC endpoints agree on it.

No keys. No signing. No funds. The tool only reads.

## The problem

An agent that acts on a single chain read will eventually act on a wrong
one. Public RPC endpoints go stale, rate-limit, or return partial data.
A single status flag can mislead. And once an agent broadcasts a
transaction or announces a result, the cost of being wrong is real:
money sent to the wrong place, or a false report the operator has to
walk back.

huju.ai answers that with a simple rule: **never act on a single read.**
Every decision passes through four gates. Each gate exists because an
unverified claim already burned us once.

## The four gates

### Gate 1 — Simulate before broadcast

Every transaction is previewed (simulated) before it is executed. If the
simulation shows risk, a wrong recipient, an unexpected approval scope,
or anything the operator did not ask for, there is no execute. The
preview is the decision, not a formality. Approvals are exact and
minimal; an unlimited approval needs a separate, explicit yes.

### Gate 2 — Two independent reads, plus a re-read

No sale, mint, or ownership change is treated as real until:

1. two independent RPC endpoints agree on `ownerOf` and on the market
   listing state,
2. a fresh re-read confirms the same state, and
3. where funds moved, the balance movement matches the claimed result.

This rule was written after the agent's sale watches cried wolf three
times on a single listing — each time a lone read, a stale endpoint, or
a bad parse produced a "sold" report that a second check disproved.
Since the rule was adopted, no result is announced from one read.

`verifier.py` in this repository is Gate 2 as a standalone tool.

### Gate 3 — One shot means one shot

For first-come-first-served mints, the firing path enforces a single
broadcast with lock files: one attempt, one transaction, no retries
after an execute, no deleting locks to "try again." A missed window
costs nothing. A double broadcast costs real money.

### Gate 4 — Fail closed

On any ambiguity — unclear input, a changed contract, an expired
preview, a read that disagrees with another read — the agent stops and
asks instead of guessing. Swaps go only through atomic router paths
with a checked minimum output, never as a direct transfer into a pool:
an earlier direct-to-pool transfer returned nothing, and that failure
mode is now designed out rather than hoped away.

## What is in this repo

- `verifier.py` — Gate 2, standalone. Given an ERC-721 contract, a
  token id, and optionally a market contract, it reads `ownerOf` and
  the market listing on two public BSC RPC endpoints and reports
  whether they agree. Read-only (`eth_call` / `eth_blockNumber` only).
  Python 3.8+ and `curl`; no Python packages, no keys, no signing.

## How to run

```sh
# Ownership + listing check on two public BSC endpoints
python3 verifier.py --nft <NFT_CONTRACT> --token <TOKEN_ID> --market <MARKET_CONTRACT>

# Ownership only
python3 verifier.py --nft <NFT_CONTRACT> --token <TOKEN_ID>

# Your own endpoints (at least two; agreement across one proves nothing)
python3 verifier.py --nft <NFT_CONTRACT> --token <TOKEN_ID> \
  --rpc https://your-endpoint-1 --rpc https://your-endpoint-2
```

The market read assumes a `listings(uint256)` function returning
`(address seller, uint256 price, bool active)`. Markets with a
differently-named read function that returns the same tuple can be
checked with `--selector <SELECTOR>`.

## What the output means

- `VERDICT: AGREE` — all endpoints report the same owner and listing
  state. Re-read before acting anyway: one agreeing snapshot is not a
  final result.
- `VERDICT: DISAGREE` — endpoints differ. Do not act on the result.
- `VERDICT: NO VERDICT` — at least one endpoint failed (rate limit,
  outage, bad response). Do not act on the result.

Exit codes: `0` agree, `2` disagree, `1` a read failed.

## Honest limitations

- This tool checks reads; it does not simulate transactions, sign
  anything, or replace Gate 1. It is one gate, not the whole agent.
- Public RPC endpoints rate-limit and go stale. When they do, the tool
  says so instead of guessing — that is the point, but it means the
  tool sometimes returns no verdict.
- The listing layout assumption (`seller, price, active`) fits the
  markets huju.ai watches. Other markets need `--selector` and, if
  their return layout differs, a small code change.
- Two agreeing endpoints can still both be wrong (same upstream, same
  stale cache). Agreement raises confidence; it does not prove truth.
  The re-read and balance-movement checks in the full gate exist for
  that reason.
- Nothing here is financial advice, and nothing here guarantees an
  outcome. Verification reduces a specific class of mistakes; it does
  not remove risk.

## License

MIT. See `LICENSE`.
