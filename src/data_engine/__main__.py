"""CLI entry point for data_engine.

Subcommands: ingest | fit | synth | ks
Implemented in task T1.3 (ingest), T1.4 (fit), T1.5 (synth), T1.6 (ks).

Usage:
    python -m data_engine ingest --trace day2 --data-dir ./Data --out-dir ./out
    python -m data_engine fit    --traces day1,day2
    python -m data_engine synth  --base day2 --n 10
    python -m data_engine ks     --real out/day2 --synth out/synthetic
"""
