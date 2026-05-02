"""python -m reporting <subcommand>

Subcommands:
    render  FR-11.1  Render per-trip HTML report
    index   FR-11.4  Render trip-list index page
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = ("render", "index")


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write(f"Usage: python -m reporting <{'|'.join(_SUBCOMMANDS)}> [args...]\n")
        sys.exit(1)

    sub = sys.argv[1]
    sys.argv = [f"reporting {sub}"] + sys.argv[2:]

    if sub == "render":
        from reporting.render import main as _main
    elif sub == "index":
        from reporting.index import main as _main
    else:
        sys.stderr.write(f"Unknown subcommand: {sub!r}. Choose: {', '.join(_SUBCOMMANDS)}\n")
        sys.exit(1)

    _main()


if __name__ == "__main__":
    main()
