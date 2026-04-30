"""python -m ideal_driver <subcommand>

Subcommands:
    match   FR-9.1  Valhalla Meili map-matching -> route_matched.parquet
    ref     FR-9.3  Road centerline extraction  -> reference_path.parquet
    speed   FR-9.4  Ideal speed profile         -> ideal_speed.parquet
    traj    FR-9.5  Quintic trajectory synthesis -> ideal_trajectory.parquet
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = ("match", "ref", "speed", "traj")


def main() -> None:
    if len(sys.argv) < 2:
        sys.stderr.write(f"Usage: python -m ideal_driver <{'|'.join(_SUBCOMMANDS)}> [args...]\n")
        sys.exit(1)

    sub = sys.argv[1]
    sys.argv = [f"ideal_driver {sub}"] + sys.argv[2:]

    if sub == "match":
        from ideal_driver.valhalla_client import main as _main
    elif sub == "ref":
        from ideal_driver.reference_path import main as _main
    elif sub == "speed":
        from ideal_driver.speed_profile import main as _main
    elif sub == "traj":
        from ideal_driver.quintic import main as _main
    else:
        sys.stderr.write(f"Unknown subcommand: {sub!r}. Choose: {', '.join(_SUBCOMMANDS)}\n")
        sys.exit(1)

    _main()


if __name__ == "__main__":
    main()
