#!/usr/bin/env python3
"""
Generate backdated git commits so they appear on a GitHub contribution graph.
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


DEFAULT_MESSAGE = (
    "If you care about a developer's github contribution graph, you are a clown"
)


@dataclass(frozen=True)
class CommitVolume:
    minimum: int
    maximum: int


def run_git(args: list[str], env: dict[str, str] | None = None) -> str:
    process = subprocess.run(
        ["git", *args],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip())
    return process.stdout.strip()


def in_git_repo() -> bool:
    process = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        check=False,
        text=True,
        capture_output=True,
    )
    return process.returncode == 0 and process.stdout.strip() == "true"


def parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date '{raw}'. Use YYYY-MM-DD.") from exc


def parse_per_day(raw: str) -> CommitVolume:
    if "-" in raw:
        parts = raw.split("-", 1)
        try:
            minimum, maximum = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise argparse.ArgumentTypeError("Use --per-day as N or MIN-MAX.") from exc
    else:
        try:
            minimum = maximum = int(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("Use --per-day as N or MIN-MAX.") from exc
    if minimum < 0 or maximum < 0 or minimum > maximum:
        raise argparse.ArgumentTypeError(
            "Commit volume must be non-negative and MIN <= MAX."
        )
    return CommitVolume(minimum, maximum)


def parse_probability(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Probability must be a number from 0 to 1.") from exc
    if value < 0 or value > 1:
        raise argparse.ArgumentTypeError("Probability must be between 0 and 1.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start",
        type=parse_date,
        default=(date.today() - timedelta(days=90)).isoformat(),
        help="Start date in YYYY-MM-DD (default: 90 days ago).",
    )
    parser.add_argument(
        "--end",
        type=parse_date,
        default=date.today().isoformat(),
        help="End date in YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--per-day",
        type=parse_per_day,
        default="0-5",
        help="Commits per day. Use N or MIN-MAX (default: 0-5). Include 0 for off days.",
    )
    parser.add_argument(
        "--distribution",
        choices=("realistic", "uniform"),
        default="realistic",
        help="Commit-count distribution. realistic adds off days and occasional spikes.",
    )
    parser.add_argument(
        "--weekday-zero-chance",
        type=parse_probability,
        default=0.30,
        help="Chance a weekday has zero commits in realistic mode (default: 0.30).",
    )
    parser.add_argument(
        "--weekend-zero-chance",
        type=parse_probability,
        default=0.65,
        help="Chance a weekend day has zero commits in realistic mode (default: 0.65).",
    )
    parser.add_argument(
        "--spike-chance",
        type=parse_probability,
        default=0.08,
        help="Chance of a higher-than-usual day in realistic mode (default: 0.08).",
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Commit message to use for every commit.",
    )
    parser.add_argument(
        "--file",
        default="contribution-log.txt",
        help="File to mutate for each generated commit.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for repeatable commit volume.",
    )
    parser.add_argument(
        "--timezone",
        default="+0000",
        help="Timezone offset used in git dates (default: +0000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing commits.",
    )
    return parser


def ensure_identity() -> None:
    try:
        run_git(["config", "user.name"])
        run_git(["config", "user.email"])
    except RuntimeError as exc:
        raise RuntimeError(
            "Git identity is not configured. Set user.name and user.email first."
        ) from exc


def maybe_init_repo() -> None:
    if in_git_repo():
        return
    run_git(["init", "-b", "main"])


def iter_days(start: date, end: date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def sample_daily_count(
    current_day: date,
    volume: CommitVolume,
    distribution: str,
    weekday_zero_chance: float,
    weekend_zero_chance: float,
    spike_chance: float,
) -> int:
    if volume.maximum == 0:
        return 0

    if distribution == "uniform":
        return random.randint(volume.minimum, volume.maximum)

    is_weekend = current_day.weekday() >= 5
    zero_chance = weekend_zero_chance if is_weekend else weekday_zero_chance
    if random.random() < zero_chance:
        return 0

    minimum_non_zero = max(1, volume.minimum)
    if minimum_non_zero > volume.maximum:
        return 0

    mode = min(
        volume.maximum,
        minimum_non_zero + max(1, (volume.maximum - minimum_non_zero) // 3),
    )
    baseline = int(round(random.triangular(minimum_non_zero, volume.maximum, mode)))

    if random.random() < spike_chance and baseline < volume.maximum:
        max_extra = max(1, (volume.maximum - minimum_non_zero) // 2)
        baseline += random.randint(1, max_extra)

    return min(baseline, volume.maximum)


def generate_timestamps(current_day: date, count: int, timezone: str) -> list[str]:
    if count <= 0:
        return []

    is_weekend = current_day.weekday() >= 5
    start_hour = 10 if is_weekend else 8
    end_hour = 21 if is_weekend else 20
    available_minutes = (end_hour - start_hour + 1) * 60
    sampled_count = min(count, available_minutes)

    minute_offsets = sorted(random.sample(range(available_minutes), sampled_count))
    timestamps: list[str] = []
    for minute_offset in minute_offsets:
        hour = start_hour + (minute_offset // 60)
        minute = minute_offset % 60
        timestamps.append(
            f"{current_day.isoformat()} {hour:02d}:{minute:02d}:00 {timezone}"
        )
    return timestamps


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if isinstance(args.start, str):
        args.start = parse_date(args.start)
    if isinstance(args.end, str):
        args.end = parse_date(args.end)
    if isinstance(args.per_day, str):
        args.per_day = parse_per_day(args.per_day)

    if args.start > args.end:
        parser.error("--start must be on or before --end.")

    maybe_init_repo()
    ensure_identity()

    if args.seed is not None:
        random.seed(args.seed)

    target_file = Path(args.file)
    commit_count = 0

    for current_day in iter_days(args.start, args.end):
        daily_count = sample_daily_count(
            current_day=current_day,
            volume=args.per_day,
            distribution=args.distribution,
            weekday_zero_chance=args.weekday_zero_chance,
            weekend_zero_chance=args.weekend_zero_chance,
            spike_chance=args.spike_chance,
        )
        for timestamp in generate_timestamps(current_day, daily_count, args.timezone):
            if args.dry_run:
                print(f"[dry-run] {timestamp} | {args.message}")
                commit_count += 1
                continue

            with target_file.open("a", encoding="utf-8") as file_handle:
                file_handle.write(f"{timestamp} entry {commit_count + 1}\n")

            run_git(["add", str(target_file)])
            env = os.environ.copy()
            env["GIT_AUTHOR_DATE"] = timestamp
            env["GIT_COMMITTER_DATE"] = timestamp
            run_git(["commit", "-m", args.message], env=env)
            commit_count += 1

    print(f"Created {commit_count} commit(s) from {args.start} to {args.end}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        raise SystemExit(1)
