"""Summarise Locust CSVs in load/results into the SLO table (D6, A7).   python3 scripts/summarise_load.py [prefix ...]"""
import csv
import glob
import os
import re
import sys

SLO = float(os.environ.get("SLO_P95_SECONDS", "30"))
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "load", "results")


def rows(prefixes):
    for path in sorted(glob.glob(os.path.join(ROOT, "*_stats.csv"))):
        name = os.path.basename(path)[: -len("_stats.csv")]
        m = re.match(r"(.+)-u(\d+)$", name)
        if not m or (prefixes and m.group(1) not in prefixes):
            continue
        for r in csv.DictReader(open(path)):
            if r["Type"] == "E2E":
                n, fails = int(r["Request Count"]), int(r["Failure Count"])
                p50, p95 = float(r["50%"]) / 1000, float(r["95%"]) / 1000
                yield (m.group(1), int(m.group(2)), n, fails, float(r["Requests/s"]), p50, p95,
                       "yes" if p95 <= SLO and fails == 0 else "NO")


def main():
    out = sorted(rows(sys.argv[1:]), key=lambda r: (r[0], r[1]))
    print(f"| run | users | runs | failed | runs/s | p50 (s) | p95 (s) | p95 <= {SLO:.0f}s? |")
    print("|---|---|---|---|---|---|---|---|")
    for r in out:
        print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]:.2f} | {r[5]:.1f} | {r[6]:.1f} | {r[7]} |")


if __name__ == "__main__":
    main()
