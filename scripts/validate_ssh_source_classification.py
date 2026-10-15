#!/usr/bin/env python3
"""Offline regression checks for the actual Alloy snippet; no live actions.

Uses ipaddress as an independent CIDR oracle, checks all IPv4 second-octet
boundaries, IPv6 ranges, source boundaries, and classified/fallback routing.
Python re executes the RE2-compatible pattern; Alloy/Loki syntax validation
must additionally be performed by their native tools before deployment.
"""

import ipaddress
import json
from pathlib import Path
import random
import re

SNIPPET = Path(__file__).with_name("ssh_source_classification.alloy")
NETWORKS = tuple(
    map(
        ipaddress.ip_network,
        (
            "192.168.1.0/24",
            "127.0.0.0/8",
            "100.64.0.0/10",
            "::1/128",
            "fe80::/10",
            "fd7a:115c:a1e0::/48",
        ),
    )
)
SELECTORS = [
    json.loads(line.split("=", 1)[1].strip())
    for line in SNIPPET.read_text().splitlines()
    if line.strip().startswith("selector =")
]
PATTERN = SELECTORS[1].split("`", 2)[1]
INTERNAL = re.compile(PATTERN)
REDACTION = re.compile(
    next(
        json.loads(line.split("=", 1)[1].strip())
        for line in SNIPPET.read_text().splitlines()
        if line.strip().startswith("expression =")
    )
)


def redact_source(message):
    # Alloy stage.replace replaces the captured source, preserving other text.
    return REDACTION.sub(
        lambda match: match[0][: match.start(1) - match.start()]
        + "REDACTED"
        + match[0][match.end(1) - match.start() :],
        message,
    )


def classify(unit, message):
    if not re.fullmatch("ssh.*", unit) or "Accepted " not in message:
        return None
    return "internal" if INTERNAL.search(message) else "external"


def alerts(zone, message):
    # Disjoint branches mirror the proposed LogQL selector union.
    return zone == "external" or (
        zone is None and "Accepted " in message and not INTERNAL.search(message)
    )


def main():
    assert SELECTORS[0] == '{unit=~"ssh.*"} |= "Accepted "'
    assert "(?=" not in PATTERN and "(?<" not in PATTERN  # no lookarounds
    assert not re.search(r"\\[1-9]", PATTERN)  # no backreferences
    addresses = [
        "192.168.1.0",
        "192.168.1.255",
        "192.168.0.255",
        "192.168.2.0",
        "127.0.0.0",
        "127.255.255.255",
        "126.255.255.255",
        "128.0.0.0",
        "::1",
        "::",
        "::2",
        "0:0:0:0:0:0:0:1",
        "0000:0000:0000:0000:0000:0000:0000:0001",
        "fe7f::1",
        "fe80::1",
        "fe90::1",
        "fea0::1",
        "febf:ffff::1",
        "fec0::1",
        "fe80::1%eth0",
        "fd7a:115c:a1e0::1",
        "fd7a:115c:a1df::1",
        "fd7a:115c:a1e1::1",
        "FD7A:115C:A1E0::FFFF",
        "2001:db8::1",
        "::ffff:100.64.0.1",
    ]
    addresses += [
        f"100.{second}.{third}.{last}"
        for second in range(256)
        for third, last in ((0, 0), (255, 255))
    ]
    randomizer = random.Random(105)
    addresses += [
        str(ipaddress.IPv4Address(randomizer.getrandbits(32))) for _ in range(500)
    ]
    count = 0
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        expected = (
            "internal" if any(ip in network for network in NETWORKS) else "external"
        )
        for auth in ("publickey", "password", "keyboard-interactive/pam"):
            message = f"Accepted {auth} for root from {address} port 22 ssh2"
            zone = classify("ssh.service", message)
            assert zone == expected, (address, zone, expected)
            assert bool(alerts(zone, message)) == (expected == "external")
            assert bool(alerts(None, message)) == (expected == "external")
            redacted = redact_source(message)
            assert redacted == f"Accepted {auth} for root from REDACTED port 22 ssh2"
            assert bool(alerts(zone, redacted)) == (expected == "external")
            count += 1
    for malformed in (
        "192.168.1.5.evil",
        "100.64.1.2evil",
        "::1bad",
        "fe80x::1",
        "fd7a:115c:a1e00::1",
        "192.168.1.999",
    ):
        assert (
            classify(
                "sshd", f"Accepted publickey for root from {malformed} port 22 ssh2"
            )
            == "external"
        )
    assert (
        classify(
            "docker.service", "Accepted publickey for root from 192.168.1.1 port 22"
        )
        is None
    )
    assert (
        classify("ssh.service", "Failed password for root from 192.168.1.1 port 22")
        is None
    )
    assert alerts(None, "Accepted publickey for root from REDACTED port 22")
    assert alerts("external", "Accepted publickey for root from 100.1.2.3 port 22")
    assert alerts("external", "Accepted publickey for root from 100.128.0.1 port 22")
    print(
        f"Passed {count} source/auth combinations plus boundaries, privacy and routing checks"
    )


if __name__ == "__main__":
    main()
