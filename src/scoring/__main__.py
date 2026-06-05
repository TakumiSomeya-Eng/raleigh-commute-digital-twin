"""python -m scoring <subcommand>

Subcommands:
    score   FR-10.7  Compute score.json for a trace
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = ("score", "run")


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write(f"Usage: python -m scoring <{'|'.join(_SUBCOMMANDS)}> [args...]\n")
        sys.exit(1)

    sub = sys.argv[1]
    # "run" is an ECS alias for "score"
    if sub == "run":
        sub = "score"
    sys.argv = [f"scoring {sub}", *sys.argv[2:]]

    if sub == "score":
        from scoring.aggregate import main as _main
    else:
        sys.stderr.write(f"Unknown subcommand: {sub!r}. Choose: {', '.join(_SUBCOMMANDS)}\n")
        sys.exit(1)

    _main()


if __name__ == "__main__":
    main()
