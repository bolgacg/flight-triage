#!/usr/bin/env python3
"""Build the flight cohort from the public PX4 log index.

The index (https://review.px4.io/dbinfo, served as a gzipped JSON from
cdn.logs.px4.io and rebuilt daily) carries one record per public log, including
the rating the uploader chose and any error labels a reviewer attached. Those
ratings are the only human judgement in this study; everything else is computed
from the logs themselves.

Groups, fixed in PREREGISTRATION.md before any log was parsed:

  case      rating == 'crash_sw_hw'   the aircraft or its software failed
  control   rating in {'good','great'}
  pilot     rating == 'crash_pilot'   a crash where the machine may be fine
  poor      rating == 'unsatisfactory'
  labelled  any reviewer error label, whatever the rating

The 'labelled' group is a separate, secondary check and never enters the
primary numbers. A reviewer who attached the label 'Vibration' to a flight was
naming the fault, so it asks a sharper question of one indicator: does the
vibration number actually rank the flights a human called vibration above the
rest?

'pilot' is the falsification group. Indicators that measure machine health
should do clearly worse at separating pilot-error crashes from controls than
they do at separating hardware and software crashes from controls. If they do
equally well on both, the indicators are detecting "a flight that ended badly"
rather than "an aircraft in trouble", and the study says so.

Writes data/cohort.json.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX_URL = "https://review.px4.io/dbinfo"
INDEX_FILE = DATA / "dbinfo.json.gz"

# Preregistered filters.
TYPES = {"Quadrotor", "Hexarotor", "Octorotor", "Fixed Wing", "VTOL Standard"}
DUR_MIN, DUR_MAX = 20.0, 600.0
CONTROLS_PER_CASE = 3
PILOT_MAX = 200
POOR_MAX = 250
LABELLED_MAX = 300
SEED = 20260914

# Duration bands used to match controls to cases.
BANDS = ((20, 60), (60, 180), (180, 600))

RATING_GROUP = {
    "crash_sw_hw": "case",
    "good": "control",
    "great": "control",
    "crash_pilot": "pilot",
    "unsatisfactory": "poor",
}

# Flight Review's error-label taxonomy, read from the select element on a log
# page (review.px4.io/plot_app?log=...), 14 Sep 2026.
ERROR_LABELS = {
    1: "Other",
    2: "Vibration",
    3: "Airframe-design",
    4: "Sensor-error",
    5: "Component-failure",
    6: "Software",
    7: "Human-error",
    8: "External-conditions",
}


def band(duration: float) -> int:
    for i, (lo, hi) in enumerate(BANDS):
        if lo <= duration < hi:
            return i
    return len(BANDS) - 1


def download_index(force: bool = False) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    if INDEX_FILE.exists() and not force:
        return INDEX_FILE
    req = urllib.request.Request(
        INDEX_URL, headers={"User-Agent": "flight-triage study (github.com/bolgacg/flight-triage)"}
    )
    with urllib.request.urlopen(req, timeout=300) as r, open(INDEX_FILE, "wb") as f:
        f.write(r.read())
    return INDEX_FILE


def eligible(rec: dict) -> bool:
    if rec.get("mav_type") not in TYPES:
        return False
    try:
        dur = float(rec.get("duration_s") or 0)
    except (TypeError, ValueError):
        return False
    if not DUR_MIN <= dur <= DUR_MAX:
        return False
    return bool(rec.get("log_id"))


def split_of(log_id: str) -> str:
    """Deterministic half, fixed before any result was read.

    The first hex digit of the md5 of the log id decides. Even digit chooses
    thresholds, odd digit measures them. Nothing about the log's content or its
    rating enters this, so the split cannot drift with the analysis.
    """
    h = hashlib.md5(log_id.encode("utf8")).hexdigest()
    return "fit" if int(h[0], 16) % 2 == 0 else "measure"


def keep(rec: dict, group: str) -> dict:
    return {
        "log_id": rec["log_id"],
        "group": group,
        "rating": rec.get("rating"),
        "log_date": rec.get("log_date"),
        "mav_type": rec.get("mav_type"),
        "airframe_name": rec.get("airframe_name"),
        "duration_s": float(rec.get("duration_s") or 0),
        "band": band(float(rec.get("duration_s") or 0)),
        "sys_hw": rec.get("sys_hw"),
        "ver_sw_release": rec.get("ver_sw_release"),
        "num_logged_errors": rec.get("num_logged_errors"),
        "num_logged_warnings": rec.get("num_logged_warnings"),
        "error_labels": sorted(rec.get("error_labels") or []),
        "error_label_names": [ERROR_LABELS.get(i, str(i)) for i in sorted(rec.get("error_labels") or [])],
        "download_url": rec.get("download_url"),
        "vehicle_uuid": rec.get("vehicle_uuid"),
        "split": split_of(rec["log_id"]),
    }


def main() -> int:
    force = "--refresh" in sys.argv
    path = download_index(force)
    with gzip.open(path, "rt") as f:
        index = json.load(f)
    print(f"index records: {len(index)}")

    by_group: dict[str, list[dict]] = {"case": [], "control": [], "pilot": [], "poor": [], "labelled": []}
    for rec in index:
        if not eligible(rec):
            continue
        group = RATING_GROUP.get(rec.get("rating") or "")
        if group:
            by_group[group].append(rec)
        elif rec.get("error_labels"):
            by_group["labelled"].append(rec)
    for g, rows in by_group.items():
        print(f"  eligible {g}: {len(rows)}")

    rng = random.Random(SEED)
    cohort: list[dict] = []

    cases = sorted(by_group["case"], key=lambda r: r["log_id"])
    for rec in cases:
        cohort.append(keep(rec, "case"))

    # Controls matched to the cases on vehicle type and duration band.
    pool: dict[tuple, list[dict]] = {}
    for rec in by_group["control"]:
        pool.setdefault((rec["mav_type"], band(float(rec["duration_s"]))), []).append(rec)
    for rows in pool.values():
        rows.sort(key=lambda r: r["log_id"])
        rng.shuffle(rows)
    taken: set[str] = set()
    shortfall = 0
    for rec in cases:
        key = (rec["mav_type"], band(float(rec["duration_s"])))
        rows = pool.get(key, [])
        picked = 0
        while rows and picked < CONTROLS_PER_CASE:
            cand = rows.pop()
            if cand["log_id"] in taken:
                continue
            taken.add(cand["log_id"])
            cohort.append(keep(cand, "control"))
            picked += 1
        shortfall += CONTROLS_PER_CASE - picked
    if shortfall:
        print(f"  note: {shortfall} control slots unfilled (no match in that type and band)")

    for group, cap in (("pilot", PILOT_MAX), ("poor", POOR_MAX)):
        rows = sorted(by_group[group], key=lambda r: r["log_id"])
        rng.shuffle(rows)
        for rec in rows[:cap]:
            cohort.append(keep(rec, group))

    # Secondary group: stratified by label so the smaller label types survive.
    by_label: dict[int, list[dict]] = {}
    for rec in by_group["labelled"]:
        for lab in rec["error_labels"]:
            by_label.setdefault(int(lab), []).append(rec)
    for rows in by_label.values():
        rows.sort(key=lambda r: r["log_id"])
        rng.shuffle(rows)
    per_label = max(1, LABELLED_MAX // max(1, len(by_label)))
    chosen: dict[str, dict] = {}
    for lab in sorted(by_label):
        for rec in by_label[lab][:per_label]:
            chosen[rec["log_id"]] = rec
    for rec in sorted(chosen.values(), key=lambda r: r["log_id"]):
        cohort.append(keep(rec, "labelled"))

    # The same flight is sometimes uploaded more than once. Those copies would
    # count twice and could land in opposite halves of the split, so one copy is
    # kept: the same aircraft, the same date and the same duration is one
    # flight. Added 14 September 2026 after duplicates appeared in the queue,
    # before the measured half was read, and recorded in PREREGISTRATION.md.
    cohort.sort(key=lambda r: (r["group"], r["log_id"]))
    seen_flight: set[tuple] = set()
    deduped: list[dict] = []
    dropped = 0
    for row in cohort:
        fid = (row.get("vehicle_uuid"), row.get("log_date"), round(row.get("duration_s") or 0))
        if fid[0] and fid in seen_flight:
            dropped += 1
            continue
        seen_flight.add(fid)
        deduped.append(row)
    if dropped:
        print(f"  dropped {dropped} repeat uploads of the same flight")
    cohort = deduped
    out = {
        "built_from": INDEX_URL,
        "filters": {
            "mav_types": sorted(TYPES),
            "duration_s": [DUR_MIN, DUR_MAX],
            "controls_per_case": CONTROLS_PER_CASE,
            "pilot_max": PILOT_MAX,
            "poor_max": POOR_MAX,
            "seed": SEED,
        },
        "error_label_taxonomy": ERROR_LABELS,
        "flights": cohort,
    }
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "cohort.json").write_text(json.dumps(out, indent=1))

    counts: dict[str, int] = {}
    splits: dict[str, int] = {}
    for row in cohort:
        counts[row["group"]] = counts.get(row["group"], 0) + 1
        splits[f'{row["group"]}/{row["split"]}'] = splits.get(f'{row["group"]}/{row["split"]}', 0) + 1
    print("\ncohort:", counts, "total", len(cohort))
    print("by split:", dict(sorted(splits.items())))
    labelled = sum(1 for r in cohort if r["error_labels"])
    print(f"with reviewer error labels: {labelled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
