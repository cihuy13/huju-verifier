#!/usr/bin/env python3
"""huju-verifier: read-only, two-RPC agreement checker for NFT ownership/listing claims.

Gate 2 of the huju.ai verification design, as a standalone tool:

    A sale, mint, or ownership result is not real until two independent
    RPC endpoints agree, a fresh re-read confirms it, and (where a
    market is involved) the listing state matches.

This script performs the first two parts. It is READ-ONLY: it only
issues eth_call / eth_blockNumber JSON-RPC requests. It never signs,
never holds keys, never spends funds, and needs no API key.

Usage:
    python3 verifier.py --nft 0x... --token 3720 --market 0x...

    --nft      ERC-721 contract address
    --token    token id (decimal)
    --market   market contract address exposing listings(uint256)
               returning (address seller, uint256 price, bool active)
               Omit --market to check ownership only.
    --rpc      JSON-RPC endpoint (repeatable; default: two public
               BSC endpoints). At least two endpoints are required:
               agreement across a single endpoint proves nothing.
    --selector listing function selector (default 0xde74e57b, the
               selector of listings(uint256)); override for markets
               with a differently-named read function that returns
               the same (seller, price, active) tuple.
    --timeout  per-request timeout in seconds (default 20)

Exit codes: 0 = all endpoints agree, 2 = endpoints disagree,
1 = a read failed (no verdict).

Requires Python 3.8+ and curl. No Python packages, no keys, no signing.
"""

import argparse
import json
import subprocess
import sys
import time

DEFAULT_RPCS = [
    "https://bsc-mainnet.public.blastapi.io",
    "https://1rpc.io/bnb",
]

OWNER_OF_SELECTOR = "0x6352211e"      # ownerOf(uint256)
LISTINGS_SELECTOR = "0xde74e57b"      # listings(uint256)


def _pad_uint256(value):
    return format(int(value), "064x")


def _decode_address(word):
    return "0x" + word[-40:]


def _rpc(rpc_url, method, params, timeout):
    """One JSON-RPC call via curl.

    curl is used instead of urllib because several public BSC endpoints
    serve chunked responses that urllib truncates (IncompleteRead).
    Retries a few times: public endpoints rate-limit aggressively.
    """
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    cmd = [
        "curl", "-sS", "-m", str(int(timeout)), "-X", "POST", rpc_url,
        "-H", "Content-Type: application/json",
        "-H", "User-Agent: huju-verifier/1.0 (read-only eth_call checker)",
        "--data-binary", payload,
    ]
    last_error = None
    for attempt in range(3):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 5
            )
            if proc.returncode != 0:
                raise RuntimeError("curl failed: %s" % proc.stderr.strip()[:200])
            body = json.loads(proc.stdout)
            if "error" in body:
                raise RuntimeError("RPC error: %s" % body["error"])
            return body["result"]
        except Exception as exc:  # noqa: BLE001 - retry, then report
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(str(last_error))


def read_owner(rpc_url, nft, token_id, timeout):
    data = OWNER_OF_SELECTOR + _pad_uint256(token_id)
    result = _rpc(rpc_url, "eth_call", [{"to": nft, "data": data}, "latest"], timeout)
    return _decode_address(result[2:])


def read_listing(rpc_url, market, token_id, selector, timeout):
    data = selector + _pad_uint256(token_id)
    result = _rpc(
        rpc_url, "eth_call", [{"to": market, "data": data}, "latest"], timeout
    )
    raw = result[2:]
    words = [raw[i : i + 64] for i in range(0, len(raw), 64)]
    if len(words) < 3:
        raise RuntimeError("unexpected listing return data: %r" % result)
    return {
        "seller": _decode_address(words[0]),
        "price_wei": int(words[1], 16),
        "active": int(words[2], 16) != 0,
    }


def read_block(rpc_url, timeout):
    return int(_rpc(rpc_url, "eth_blockNumber", [], timeout), 16)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Two-RPC NFT ownership/listing checker.")
    parser.add_argument("--nft", required=True, help="ERC-721 contract address")
    parser.add_argument("--token", required=True, type=int, help="token id (decimal)")
    parser.add_argument("--market", help="market contract address (optional)")
    parser.add_argument("--rpc", action="append", default=[], help="RPC endpoint (repeatable)")
    parser.add_argument("--selector", default=LISTINGS_SELECTOR, help="listing read selector")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    rpcs = args.rpc or DEFAULT_RPCS
    if len(rpcs) < 2:
        print("error: at least two --rpc endpoints are required", file=sys.stderr)
        return 1

    print("huju-verifier: checking token %d" % args.token)
    print("  nft:    %s" % args.nft)
    if args.market:
        print("  market: %s" % args.market)
    print()

    reads = []
    failed = False
    for rpc_url in rpcs:
        try:
            owner = read_owner(rpc_url, args.nft, args.token, args.timeout)
            block = read_block(rpc_url, args.timeout)
            listing = None
            if args.market:
                listing = read_listing(
                    rpc_url, args.market, args.token, args.selector, args.timeout
                )
            reads.append({"rpc": rpc_url, "owner": owner, "block": block, "listing": listing})
            print("[%s] block %d" % (rpc_url, block))
            print("  ownerOf(%d) = %s" % (args.token, owner))
            if listing is not None:
                print(
                    "  listing: seller=%s price_wei=%d active=%s"
                    % (listing["seller"], listing["price_wei"], listing["active"])
                )
        except Exception as exc:  # noqa: BLE001 - report and continue with other RPCs
            failed = True
            print("[%s] READ FAILED: %s" % (rpc_url, exc))
        print()

    if failed or len(reads) < 2:
        print("VERDICT: NO VERDICT - at least one endpoint failed; do not act on this result.")
        return 1

    owners = {r["owner"] for r in reads}
    listings = {
        (r["listing"]["seller"], r["listing"]["price_wei"], r["listing"]["active"])
        if r["listing"] is not None
        else None
        for r in reads
    }
    if len(owners) == 1 and len(listings) == 1:
        print("VERDICT: AGREE - %d endpoints report the same owner and listing state." % len(reads))
        print("Re-read before acting: a single agreeing snapshot is not a final result.")
        return 0
    print("VERDICT: DISAGREE - endpoints report different state; do not act on this result.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
