"""Find out why Claude calls are failing on this host.

Run it where the failure happens — the Render shell, not a laptop:

    python check_anthropic.py

The reason this script exists: the Anthropic SDK raises APIConnectionError,
which stringifies to exactly "Connection error." and nothing else. That one
sentence covers DNS failure, TLS failure, a dead outbound proxy, IPv6 with no
route, and a firewall — five different problems with five different fixes. The
real exception is hanging off __cause__, so this walks the chain and prints it.

Nothing here is destructive and nothing is printed that would be unsafe to
paste into a chat: the key is reported by shape and last four characters only.
"""

from __future__ import annotations

import os
import socket
import ssl
import sys
import time

HOST = "api.anthropic.com"


def line(label: str, value: str) -> None:
    print(f"{label:<22} {value}", flush=True)


def describe_chain(exc: BaseException) -> str:
    parts, seen = [], set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        parts.append(f"{type(current).__name__}: {text}" if text
                     else type(current).__name__)
        current = current.__cause__ or current.__context__
    return "\n                       <- ".join(parts)


def check_key() -> str | None:
    raw = os.getenv("ANTHROPIC_API_KEY")
    if raw is None:
        line("API key", "NOT SET — this is the whole problem")
        return None
    if raw != raw.strip():
        line("API key", "set, but has leading/trailing whitespace — "
                        "a pasted newline will cause a 401")
    key = raw.strip()
    if not key:
        line("API key", "set but empty")
        return None
    shape = "looks right" if key.startswith("sk-ant-") else \
            "does NOT start with sk-ant- — wrong value pasted?"
    line("API key", f"set, {len(key)} chars, ends ...{key[-4:]}, {shape}")
    return key


def check_proxy() -> None:
    """A stale proxy variable is a common and very confusing cause.

    httpx honours these; some other HTTP clients don't. That asymmetry is how
    you end up with a service that reaches Sleeper perfectly and cannot reach
    Anthropic at all, which looks like an Anthropic outage and isn't.
    """
    found = {k: v for k in ("HTTPS_PROXY", "https_proxy",
                            "HTTP_PROXY", "http_proxy", "ALL_PROXY", "NO_PROXY")
             if (v := os.getenv(k))}
    line("Proxy env", str(found) if found else "none set (normal)")


def check_dns() -> None:
    try:
        infos = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:  # noqa: BLE001
        line("DNS", f"FAILED — {describe_chain(exc)}")
        return
    addrs = sorted({info[4][0] for info in infos})
    v6 = [a for a in addrs if ":" in a]
    line("DNS", f"{len(addrs)} address(es): {', '.join(addrs[:4])}")
    if v6:
        line("", f"includes IPv6 ({v6[0]}) — if the host has no IPv6 route, "
                 f"that alone produces 'Connection error.'")


def check_tcp_tls() -> None:
    try:
        started = time.time()
        with socket.create_connection((HOST, 443), timeout=10) as sock:
            line("TCP :443", f"connected in {round(time.time() - started, 2)}s")
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(sock, server_hostname=HOST) as tls:
                line("TLS", f"ok, {tls.version()}")
    except Exception as exc:  # noqa: BLE001
        line("TCP/TLS", f"FAILED — {describe_chain(exc)}")


def check_call(key: str) -> None:
    try:
        import anthropic
    except ImportError as exc:
        line("SDK", f"not installed — {exc}")
        return
    line("SDK version", getattr(anthropic, "__version__", "unknown"))

    client = anthropic.Anthropic(api_key=key)
    try:
        started = time.time()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the word: ok"}],
        )
    except Exception as exc:  # noqa: BLE001
        line("Live call", f"FAILED — {describe_chain(exc)}")
        return
    took = round(time.time() - started, 2)
    line("Live call", f"ok in {took}s — {message.content[0].text.strip()!r}")


def main() -> int:
    print(f"Checking Claude API reachability from this host\n{'-' * 60}")
    line("Python", sys.version.split()[0])
    key = check_key()
    check_proxy()
    check_dns()
    check_tcp_tls()
    if key:
        check_call(key)
    print("-" * 60)
    print("Any line above that says FAILED is the answer. If every line passes "
          "but generation still fails in the app, the app and this script are "
          "reading different environments — check that the key is set on the "
          "web service itself and not only on the cron job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
