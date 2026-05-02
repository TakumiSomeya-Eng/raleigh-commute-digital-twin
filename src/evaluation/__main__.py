"""python -m evaluation <subcommand>

Subcommands:
    smooth   FR-6.1  RTS smoother -> ground_truth.parquet
    rmse     FR-6.2  RMSE harness -> rmse_report_<filter>.json
    compare  FR-6.4  EKF vs UKF comparator -> filter_comparison.json
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = ("smooth", "rmse", "compare")


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write(f"Usage: python -m evaluation <{'|'.join(_SUBCOMMANDS)}> [args...]\n")
        sys.exit(1)

    sub = sys.argv[1]
    sys.argv = [f"evaluation {sub}"] + sys.argv[2:]

    if sub == "smooth":
        from evaluation.rts_smoother import main as _main
    elif sub == "rmse":
        from evaluation.rmse import main as _main
    elif sub == "compare":
        from evaluation.comparator import main as _main
    else:
        sys.stderr.write(f"Unknown subcommand: {sub!r}. Choose: {', '.join(_SUBCOMMANDS)}\n")
        sys.exit(1)

    _main()


if __name__ == "__main__":
    main()
