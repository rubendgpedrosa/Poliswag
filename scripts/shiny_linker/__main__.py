"""python -m scripts.shiny_linker [--check | --validate]

(no flag)   resolve pending codes if every guard holds; what the timer runs
--check     print each guard's state and change nothing
--validate  after a game update: resolve the known scanner account
            (SHINY_LINKER_SELFTEST_*) and compare the ID
"""

import argparse
import fcntl
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[2]
LOCK = "/run/lock/shiny-linker.lock"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shiny-linker", description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(REPO / ".env")
    # Imported after the environment is loaded: phone.py and store.py read it.
    from .linker import Linker
    from .store import Store

    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("outra ligação está a correr")
            return 0
        linker = Linker(Store(), os.environ)
        if args.check:
            checks, _ = linker.survey(validate=False)
            for name, ok, detail in checks:
                print(
                    f"{'ok ' if ok else 'NÃO'}  {name}"
                    + (f"  ({detail})" if detail else "")
                )
            return 0
        outcome = linker.run(validate=args.validate)
        print(outcome)
        return 1 if "FALHOU" in outcome or "DESLIGADO" in outcome else 0


if __name__ == "__main__":
    sys.exit(main())
