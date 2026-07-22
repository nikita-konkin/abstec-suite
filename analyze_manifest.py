#!/usr/bin/env python3
"""Summarise an absolTEC run manifest, and test the malformed-input hypothesis.

Two uses:

  # What happened in the last batch?
  python analyze_manifest.py /data/out/_manifest.csv

  # Would strict .dat validation have predicted the absolTEC crashes?
  python analyze_manifest.py /data/out/_manifest.csv --dat-path /data/in

The second form is the experiment worth running. absolTEC aborts with
"forrtl: severe (64): input conversion error" when its fixed-format Fortran READ
meets a line it cannot parse. `--strict-dat-validation` was built on the theory
that such rows are detectable in Python beforehand, but that theory was never
confirmed against real failures. This compares the two sets and prints the
confusion matrix, which either justifies enabling strict validation by default
or shows the crashes come from something else.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from run_absoltec import find_matching_dat_files, resolve_station_data_folder, scan_dat_file

RUNTIME_FAILURE_STATUSES = {"failed-runtime", "failed-wine"}


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def summarise(rows: list[dict[str, str]]) -> None:
    statuses = Counter(row.get("status", "?") for row in rows)
    total = len(rows)
    print(f"Stations recorded: {total}\n")
    print("By status:")
    for status, count in statuses.most_common():
        print(f"  {status:<24} {count:>7}  ({count / total:5.1%})")

    durations = []
    for row in rows:
        try:
            durations.append(float(row["duration_seconds"]))
        except (KeyError, ValueError, TypeError):
            continue
    if durations:
        durations.sort()
        mid = durations[len(durations) // 2]
        print(
            f"\nDuration: median {mid:.1f}s, min {durations[0]:.1f}s, "
            f"max {durations[-1]:.1f}s, total {sum(durations) / 3600:.1f}h of station time"
        )

    failures = [r for r in rows if r.get("status") in RUNTIME_FAILURE_STATUSES]
    if failures:
        print(f"\nTop failure reasons ({len(failures)} failures):")
        reasons = Counter(_reason_signature(r.get("reason", "")) for r in failures)
        for reason, count in reasons.most_common(10):
            print(f"  {count:>5}  {reason[:110]}")


def _reason_signature(reason: str) -> str:
    """Collapse a reason to something groupable (drop job ids and paths)."""
    words = []
    for word in reason.split():
        if len(word) > 24 or "/" in word or "\\" in word:
            continue
        words.append(word)
    return " ".join(words[:14]) or reason[:80]


def check_hypothesis(rows: list[dict[str, str]], dat_root: Path) -> None:
    """Compare stations strict validation would reject against ones that failed."""
    predicted_bad: set[tuple[str, str]] = set()
    actually_failed: set[tuple[str, str]] = set()
    examined = 0
    missing = 0

    for row in rows:
        try:
            year = int(row["year"])
            doy = int(row["day_of_year"])
        except (KeyError, ValueError):
            continue
        site = row.get("site", "")
        key = (row["day_of_year"], site)

        if row.get("status") in RUNTIME_FAILURE_STATUSES:
            actually_failed.add(key)

        folder = resolve_station_data_folder(dat_root, site, doy, year)
        if not folder.exists():
            missing += 1
            continue
        matches, _pattern = find_matching_dat_files(folder, site, doy, year)
        if not matches:
            missing += 1
            continue

        examined += 1
        for dat_file in matches:
            try:
                _rows, _nonzero, problems = scan_dat_file(dat_file)
            except OSError:
                continue
            if problems:
                predicted_bad.add(key)
                break

    hits = predicted_bad & actually_failed
    false_alarms = predicted_bad - actually_failed
    misses = actually_failed - predicted_bad

    print(f"\n{'=' * 62}\nStrict-validation hypothesis\n{'=' * 62}")
    print(f"Stations examined on disk:        {examined} ({missing} inputs no longer present)")
    print(f"absolTEC failures in manifest:    {len(actually_failed)}")
    print(f"Flagged by strict validation:     {len(predicted_bad)}")
    print(f"  caught a real failure:          {len(hits)}")
    print(f"  would skip a station that ran:  {len(false_alarms)}")
    print(f"  failures it did NOT predict:    {len(misses)}")

    if not actually_failed:
        print("\nNo runtime failures recorded - nothing to test against yet.")
        return

    recall = len(hits) / len(actually_failed)
    print(f"\nRecall (failures predicted): {recall:.0%}")
    if predicted_bad:
        precision = len(hits) / len(predicted_bad)
        print(f"Precision (flags that were real): {precision:.0%}")

    if recall >= 0.8 and not false_alarms:
        print(
            "\nVERDICT: the theory holds. Enable --strict-dat-validation to turn these\n"
            "crashes into cheap pre-flight skips."
        )
    elif recall < 0.3:
        print(
            "\nVERDICT: the theory is wrong - most crashes are NOT caused by rows that\n"
            "Python can see are malformed. Look elsewhere (header-line counts, arc\n"
            "lengths, or absolTEC's own limits). Keep strict validation off."
        )
    else:
        print(
            "\nVERDICT: partial. Strict validation catches some crashes but not all, and\n"
            f"would wrongly skip {len(false_alarms)} working station(s). Inspect the\n"
            "misses below before enabling it."
        )

    for label, group in (("Not predicted", misses), ("False alarms", false_alarms)):
        if group:
            sample = ", ".join(f"{d}/{s}" for d, s in sorted(group)[:10])
            print(f"  {label} (first 10): {sample}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", help="Path to _manifest.csv written by run_absoltec.py")
    parser.add_argument(
        "--dat-path",
        help="Input DAT root. When given, tests whether strict validation predicts the failures.",
    )
    args = parser.parse_args()

    rows = read_manifest(Path(args.manifest))
    if not rows:
        print("Manifest is empty.")
        return

    summarise(rows)
    if args.dat_path:
        check_hypothesis(rows, Path(args.dat_path))


if __name__ == "__main__":
    main()
