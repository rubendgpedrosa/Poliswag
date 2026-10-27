#!/usr/bin/env python3
"""Refuse game-side and interception code in Poliswag.

Poliswag is the Discord bridge between services: Discord in, HTTP to PoliSnatch
and the scanner stack, the database out. What lives inside the game, or in front
of its traffic, belongs to PoliSnatch (CT 108): native hooks and injector
payloads, the game's own opcodes and the fields they return, and the
instrumentation or proxy tooling around them.

    python3 scripts/check_private_tech.py <paths...>   # what pre-commit passes
    python3 scripts/check_private_tech.py --all        # every tracked file (CI)

A line may opt out with `poliswag-guard: allow` and a reason; a path never can.
The patterns below are assembled from pieces on purpose, so this file does not
spell them out either — the repository should not carry them at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", "logs", "data"}
SKIP_PATHS = {
    "scripts/check_private_tech.py",
    "tests/scripts/test_check_private_tech.py",
}
MAX_BYTES = 2_000_000
ALLOW_MARK = "poliswag-guard: allow"

PATH_RULES = [
    (re.compile(r"(?:^|/)injector[^/]*$", re.I), "an injector payload"),
    (
        re.compile(r"native-method-" r"redirect\.js$", re.I),
        "a game hook script",
    ),
    (
        re.compile(
            r"(?:^|/)scripts/shiny_linker/"
            r"(?:phone|lobby|proto|rotom|remote|linker|status)\.py$",
            re.I,
        ),
        "host-side game session code",
    ),
    (
        re.compile(r"(?:^|/)scripts/shiny-linker(?:\.service|\.timer)?$", re.I),
        "a host linker unit",
    ),
]

TEXT_RULES = [
    (
        re.compile(r"\b" r"invited_" r"player_ids\b", re.I),
        "a field a game RPC returns",
    ),
    (
        re.compile(r"\b" r"social_" r"actions\b", re.I),
        "social RPC action ids",
    ),
    (re.compile(r"native-method-" r"redirect", re.I), "a game hook script"),
    (re.compile(r"pokemod-" r"injector", re.I), "a game injector"),
    (re.compile(r"\b" "frida" r"\b", re.I), "an instrumentation framework"),
    (
        re.compile(r"\b(?:zygisk|lsposed|xposed)\b", re.I),
        "an instrumentation loader",
    ),
    (re.compile(r"\b" "mitm" "proxy" r"\b", re.I), "an interception proxy"),
    (re.compile(r"\b" "501" "2" r"\b"), "a game opcode this repo has no use for"),
    (re.compile(r"\b" "get_" "player" r"\b", re.I), "a game RPC name"),
]


def tracked():
    """Every file git knows about, from the repository root."""
    done = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    return [name for name in done.stdout.decode().split("\0") if name]


def scan_text(name, text):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for rule, what in TEXT_RULES:
            if rule.search(line):
                findings.append((name, number, what, line.strip()[:120]))
    return findings


def check(paths):
    findings = []
    for name in paths:
        if name in SKIP_PATHS:
            continue
        if any(part in SKIP_DIRS for part in Path(name).parts):
            continue
        for rule, what in PATH_RULES:
            if rule.search(name):
                findings.append((name, 0, what, ""))
        try:
            if Path(name).stat().st_size > MAX_BYTES:
                continue
            text = Path(name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(name, text))
    return findings


def report(findings):
    if not findings:
        return 0
    print("This repository is the Discord bridge, not the game side.")
    print("Game hooks, injectors, opcodes and interception belong in PoliSnatch.\n")
    for name, line, what, text in findings:
        where = f"{name}:{line}" if line else name
        detail = f"  {text}" if text else ""
        print(f"{where}: {what}{detail}")
    print(
        "\nMove it to PoliSnatch (CT 108). If this is a false positive, keep the "
        f"line and add `# {ALLOW_MARK}` with a short reason."
    )
    return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--all" in argv:
        paths = tracked()
    else:
        paths = [arg for arg in argv if not arg.startswith("-")]
        if not paths:
            print("usage: check_private_tech.py <paths...> | --all", file=sys.stderr)
            return 2
    return report(check(paths))


if __name__ == "__main__":
    raise SystemExit(main())
