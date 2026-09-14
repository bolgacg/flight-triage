#!/usr/bin/env python3
"""Choose thresholds on the fit half, measure them on the other half, once.

Everything this script does was fixed in PREREGISTRATION.md before it existed.
It writes data/results.json, which is the only thing the page reads.

Usage:
    python3 study/score.py                 # the real run
    python3 study/score.py --fit-only      # look at the fit half alone
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FALSE_ALARM_BUDGET = 0.10

# name -> (direction, tier, plain-language label, unit)
# direction +1 means a high value is the worrying one.
INDICATORS = {
    "vib_hf_ms2": (+1, 1, "Vibration", "m/s²"),
    "est_reject_frac": (+1, 1, "Estimator rejecting measurements", "share of flight"),
    "est_ratio_p95": (+1, 1, "Worst estimator test ratio", "ratio"),
    "est_fault_frac": (+1, 1, "Estimator fault flag raised", "share of flight"),
    "track_err_p95": (+1, 1, "Rate tracking error", "rad/s"),
    "batt_sag_ohm": (+1, 1, "Pack internal resistance", "ohm"),
    "batt_min_cell_v": (-1, 1, "Lowest cell voltage under load", "V"),
    "gps_bad_frac": (+1, 1, "Time without a 3D satellite fix", "share of flight"),
    "fd_any_frac": (+1, 2, "PX4's own failure detector", "share of flight"),
    "vib_accel_p95": (+1, 2, "PX4's own vibration metric", "m/s²"),
    "sat_frac": (+1, 2, "Motor output at its limit", "share of flight"),
    "unalloc_torque_p95": (+1, 2, "Torque the mixer could not deliver", "norm"),
    "accel_inconsistency_p95": (+1, 2, "Accelerometers disagreeing", "m/s²"),
    "accel_clip_per_min": (+1, 2, "Accelerometer clipping", "events/min"),
    "imbalanced_prop_p95": (+1, 2, "Imbalanced propeller metric", "metric"),
}

TIER1 = [k for k, v in INDICATORS.items() if v[1] == 1]


def wilson(k: int, n: int) -> tuple[float, float]:
    """95 percent interval for a proportion, usable at small n."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963985
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def load() -> list[dict]:
    cohort = {f["log_id"]: f for f in json.loads((DATA / "cohort.json").read_text())["flights"]}
    rows: list[dict] = []
    seen: set[str] = set()
    for line in (DATA / "features.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut short by an interrupted run
        lid = rec.get("log_id")
        if not lid or lid in seen or lid not in cohort:
            continue
        seen.add(lid)
        if not rec.get("ok"):
            continue
        row = dict(cohort[lid])
        row.update({k: v for k, v in rec.items() if k != "log_id"})
        rows.append(row)
    return rows


def values(rows: list[dict], name: str) -> np.ndarray:
    sign = INDICATORS[name][0]
    out = []
    for r in rows:
        v = r.get(name)
        out.append(np.nan if v is None else sign * float(v))
    return np.array(out, dtype=float)


def threshold_at(controls: list[dict], name: str, quantile: float) -> float | None:
    v = values(controls, name)
    v = v[np.isfinite(v)]
    if v.size < 30:
        return None
    return float(np.quantile(v, quantile))


def flagged(rows: list[dict], name: str, thr: float | None) -> tuple[int, int]:
    """(flagged, with a value). A flight whose indicator is missing is not
    flagged and is not counted."""
    if thr is None:
        return (0, 0)
    v = values(rows, name)
    ok = np.isfinite(v)
    return (int(np.count_nonzero(v[ok] > thr)), int(np.count_nonzero(ok)))


def rate_block(rows: list[dict], name: str, thr: float | None) -> dict:
    k, n = flagged(rows, name, thr)
    lo, hi = wilson(k, n)
    return {"flagged": k, "n": n, "rate": (k / n) if n else None, "lo": lo, "hi": hi}


def combined_flags(rows: list[dict], thresholds: dict[str, float | None]) -> np.ndarray:
    hit = np.zeros(len(rows), dtype=bool)
    for name, thr in thresholds.items():
        if thr is None:
            continue
        v = values(rows, name)
        hit |= np.isfinite(v) & (v > thr)
    return hit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit-only", action="store_true")
    args = ap.parse_args()

    rows = load()
    by = lambda g, s: [r for r in rows if r["group"] == g and (s is None or r["split"] == s)]

    counts = {}
    for g in ("case", "control", "pilot", "poor", "labelled"):
        counts[g] = {"fit": len(by(g, "fit")), "measure": len(by(g, "measure")), "total": len(by(g, None))}
    print("parsed flights by group:", {g: c["total"] for g, c in counts.items()})

    fit_controls = by("control", "fit")
    meas_controls = by("control", "measure")

    # Per-indicator thresholds at the false-alarm budget, chosen on the fit half.
    thresholds = {n: threshold_at(fit_controls, n, 1 - FALSE_ALARM_BUDGET) for n in INDICATORS}

    coverage = {}
    for n in INDICATORS:
        v = values(rows, n)
        coverage[n] = float(np.count_nonzero(np.isfinite(v)) / max(1, len(rows)))

    per_indicator = {}
    for n, (sign, tier, label, unit) in INDICATORS.items():
        block = {
            "label": label,
            "unit": unit,
            "tier": tier,
            "direction": "high" if sign > 0 else "low",
            "threshold": (sign * thresholds[n]) if thresholds[n] is not None else None,
            "coverage": coverage[n],
            "fit": {g: rate_block(by(g, "fit"), n, thresholds[n]) for g in ("case", "control", "pilot", "poor")},
        }
        if not args.fit_only:
            block["measure"] = {
                g: rate_block(by(g, "measure"), n, thresholds[n]) for g in ("case", "control", "pilot", "poor")
            }
        per_indicator[n] = block

    # The combined rule: one quantile for every first-tier indicator, chosen on
    # the fit half so that the whole rule spends the false-alarm budget once.
    best = None
    scan = []
    for q in np.arange(0.80, 0.9991, 0.0025):
        thr = {n: threshold_at(fit_controls, n, float(q)) for n in TIER1}
        fa = float(np.mean(combined_flags(fit_controls, thr))) if fit_controls else 1.0
        scan.append({"q": round(float(q), 4), "fit_false_alarm": fa})
        if best is None or abs(fa - FALSE_ALARM_BUDGET) < abs(best[1] - FALSE_ALARM_BUDGET):
            best = (float(q), fa, thr)
    q_star, fa_fit, thr_star = best
    print(f"combined rule: q={q_star:.4f}, false alarms on the fit half {fa_fit:.3f}")

    combined = {
        "q": q_star,
        "fit_false_alarm": fa_fit,
        "thresholds": {n: (INDICATORS[n][0] * t) if t is not None else None for n, t in thr_star.items()},
        "fit": {},
    }
    for g in ("case", "control", "pilot", "poor"):
        rs = by(g, "fit")
        hit = combined_flags(rs, thr_star)
        k, n = int(np.count_nonzero(hit)), len(rs)
        lo, hi = wilson(k, n)
        combined["fit"][g] = {"flagged": k, "n": n, "rate": (k / n) if n else None, "lo": lo, "hi": hi}
    if not args.fit_only:
        combined["measure"] = {}
        for g in ("case", "control", "pilot", "poor"):
            rs = by(g, "measure")
            hit = combined_flags(rs, thr_star)
            k, n = int(np.count_nonzero(hit)), len(rs)
            lo, hi = wilson(k, n)
            combined["measure"][g] = {"flagged": k, "n": n, "rate": (k / n) if n else None, "lo": lo, "hi": hi}

    # A second combining rule, decided AFTER the fit half was looked at and
    # labelled as such on the page and in the registration. The preregistered
    # rule spends one budget across eight indicators, which forces each of them
    # to a strict threshold. This one asks instead for agreement: flag a flight
    # when at least k indicators sit above their own threshold. Both k and the
    # per-indicator quantile are chosen on the fit half; the measured half is
    # still read only once.
    def count_flags(rs: list[dict], thr: dict[str, float | None], k: int) -> np.ndarray:
        n = np.zeros(len(rs), dtype=int)
        for name, t in thr.items():
            if t is None:
                continue
            v = values(rs, name)
            n += (np.isfinite(v) & (v > t)).astype(int)
        return n >= k

    best_k = None
    for qq in np.arange(0.60, 0.981, 0.01):
        thr = {n: threshold_at(fit_controls, n, float(qq)) for n in TIER1}
        for k in range(1, len(TIER1) + 1):
            fa = float(np.mean(count_flags(fit_controls, thr, k))) if fit_controls else 1.0
            det = float(np.mean(count_flags(by("case", "fit"), thr, k))) if by("case", "fit") else 0.0
            if fa <= FALSE_ALARM_BUDGET + 0.005:
                score = det
                if best_k is None or score > best_k[0]:
                    best_k = (score, float(qq), k, fa, thr)
    combined_k = None
    if best_k:
        _, qq, k, fa, thr = best_k
        combined_k = {
            "not_preregistered": True,
            "q": qq,
            "k": k,
            "fit_false_alarm": fa,
            "thresholds": {n: (INDICATORS[n][0] * t) if t is not None else None for n, t in thr.items()},
            "fit": {},
        }
        for g in ("case", "control", "pilot", "poor"):
            rs = by(g, "fit")
            hit = count_flags(rs, thr, k)
            kk, nn = int(np.count_nonzero(hit)), len(rs)
            lo, hi = wilson(kk, nn)
            combined_k["fit"][g] = {"flagged": kk, "n": nn, "rate": (kk / nn) if nn else None, "lo": lo, "hi": hi}
        if not args.fit_only:
            combined_k["measure"] = {}
            for g in ("case", "control", "pilot", "poor"):
                rs = by(g, "measure")
                hit = count_flags(rs, thr, k)
                kk, nn = int(np.count_nonzero(hit)), len(rs)
                lo, hi = wilson(kk, nn)
                combined_k["measure"][g] = {"flagged": kk, "n": nn, "rate": (kk / nn) if nn else None, "lo": lo, "hi": hi}
        print(f"agreement rule (not preregistered): at least {k} of {len(TIER1)} over the {qq:.2f} quantile, "
              f"false alarms on the fit half {fa:.3f}")

    # Baselines.
    baselines = {}
    modern = [r for r in rows if r.get("tier") == "modern"]
    for split in (["fit"] if args.fit_only else ["fit", "measure"]):
        b = {}
        for g in ("case", "control", "pilot", "poor"):
            rs = [r for r in modern if r["group"] == g and r["split"] == split]
            k = sum(1 for r in rs if (r.get("fd_any_frac") or 0) > 0)
            lo, hi = wilson(k, len(rs))
            b[g] = {"flagged": k, "n": len(rs), "rate": (k / len(rs)) if rs else None, "lo": lo, "hi": hi}
        baselines[f"failure_detector_{split}"] = b
        b2 = {}
        for g in ("case", "control", "pilot", "poor"):
            rs = [r for r in rows if r["group"] == g and r["split"] == split]
            k = sum(1 for r in rs if (r.get("num_logged_errors") or 0) > 0)
            lo, hi = wilson(k, len(rs))
            b2[g] = {"flagged": k, "n": len(rs), "rate": (k / len(rs)) if rs else None, "lo": lo, "hi": hi}
        baselines[f"logged_errors_{split}"] = b2

    # Secondary check: where do the flights a reviewer called Vibration sit in
    # the vibration distribution of the whole cohort?
    vib_all = values(rows, "vib_hf_ms2")
    finite = vib_all[np.isfinite(vib_all)]
    secondary = {}
    for label in ("Vibration", "Sensor-error", "Component-failure", "Software", "Human-error"):
        idx = [i for i, r in enumerate(rows) if label in (r.get("error_label_names") or [])]
        vals = [vib_all[i] for i in idx if np.isfinite(vib_all[i])]
        if vals and finite.size:
            pct = [float((finite < v).mean()) for v in vals]
            secondary[label] = {
                "n": len(vals),
                "median_percentile": float(np.median(pct)),
                "share_above_median": float(np.mean([p > 0.5 for p in pct])),
            }
    # How well the self-computed vibration agrees with PX4's own metric, on the
    # flights that carry both.
    a = values(rows, "vib_hf_ms2")
    b = values(rows, "vib_accel_p95")
    both = np.isfinite(a) & np.isfinite(b)
    agreement = None
    if int(np.count_nonzero(both)) > 20:
        agreement = {
            "n": int(np.count_nonzero(both)),
            "spearman": float(
                np.corrcoef(
                    np.argsort(np.argsort(a[both])).astype(float),
                    np.argsort(np.argsort(b[both])).astype(float),
                )[0, 1]
            ),
        }

    # The budget curve behind the page's slider. The headline stays the 10
    # percent budget fixed in the preregistration; this shows what the same
    # rules do at other budgets, and is labelled on the page as exploration.
    curve = []
    r4 = lambda x: None if x is None else round(float(x), 4)
    for q in np.arange(0.50, 0.9951, 0.01):
        thr1 = {n: threshold_at(fit_controls, n, float(q)) for n in TIER1}
        entry = {"q": round(float(q), 3), "combined": {}, "agree": {}}
        for g in ("case", "control", "pilot", "poor"):
            rs = by(g, "measure") if not args.fit_only else by(g, "fit")
            entry["combined"][g] = r4(np.mean(combined_flags(rs, thr1))) if len(rs) else None
            if combined_k:
                entry["agree"][g] = r4(np.mean(count_flags(rs, thr1, combined_k["k"]))) if len(rs) else None
        curve.append(entry)

    # A triage queue the reader can look through: every measured flight, its
    # indicator values, and what the human said afterwards.
    queue = []
    for r in (rows if args.fit_only else [x for x in rows if x["split"] == "measure"]):
        item = {
            "log_id": r["log_id"],
            "group": r["group"],
            "rating": r.get("rating"),
            "labels": r.get("error_label_names") or [],
            "mav_type": r.get("mav_type"),
            "airframe": r.get("airframe_name"),
            "date": r.get("log_date"),
            "duration_s": r.get("duration_s"),
            "firmware": (r.get("ver_sw_release") or "").split()[0] if r.get("ver_sw_release") else None,
            "tier": r.get("tier"),
            "armed_s": r.get("armed_s"),
            "logged_errors": r.get("num_logged_errors"),
            "fd_flags": r.get("fd_flags") or [],
        }
        for n in INDICATORS:
            v = r.get(n)
            item[n] = None if v is None else round(float(v), 4)
        queue.append(item)

    tiers = {}
    for t in ("legacy", "modern"):
        tiers[t] = {g: sum(1 for r in rows if r.get("tier") == t and r["group"] == g) for g in ("case", "control", "pilot", "poor", "labelled")}

    out = {
        "false_alarm_budget": FALSE_ALARM_BUDGET,
        "counts": counts,
        "tiers": tiers,
        "indicators": per_indicator,
        "combined": combined,
        "combined_k": combined_k,
        "baselines": baselines,
        "secondary_error_labels": secondary,
        "vibration_agreement": agreement,
        "curve": curve,
        "queue": queue,
        "fit_only": args.fit_only,
    }
    name = "results_fit.json" if args.fit_only else "results.json"
    (DATA / name).write_text(json.dumps(out, indent=1))
    print(f"wrote data/{name}")

    half = "fit" if args.fit_only else "measure"
    print(f"\n{'indicator':34s} {'thr':>10s} {'cov':>5s} {'case':>14s} {'control':>9s} {'pilot':>9s}")
    for n, blk in per_indicator.items():
        if blk["threshold"] is None:
            continue
        h = blk.get(half) or blk["fit"]
        c, ctl, p = h["case"], h["control"], h["pilot"]
        f = lambda d: f'{d["rate"]:.2f}({d["n"]})' if d["rate"] is not None else "   -   "
        print(f'{blk["label"][:33]:34s} {blk["threshold"]:10.3g} {blk["coverage"]:5.2f} {f(c):>14s} {f(ctl):>9s} {f(p):>9s}')
    fmt = lambda d: (f'{d["rate"]:.2f}({d["n"]})' if d and d.get("rate") is not None else "   -   ")
    h = combined.get(half) or combined["fit"]
    print(f'{"COMBINED (any first-tier)":34s} {"q=%.3f" % q_star:>10s} {"":5s} '
          f'{fmt(h["case"]):>14s} {fmt(h["control"]):>9s} {fmt(h["pilot"]):>9s}')
    if combined_k:
        hk = combined_k.get(half) or combined_k["fit"]
        print(f'{"AGREEMENT (>=%d of 8, exploratory)" % combined_k["k"]:34s} {"q=%.2f" % combined_k["q"]:>10s} {"":5s} '
              f'{fmt(hk["case"]):>14s} {fmt(hk["control"]):>9s} {fmt(hk["pilot"]):>9s}')
    for key in (f"failure_detector_{half}", f"logged_errors_{half}"):
        b = baselines.get(key)
        if b:
            print(f'{key:34s} {"":10s} {"":5s} '
                  f'{fmt(b["case"]):>14s} {fmt(b["control"]):>9s} {fmt(b["pilot"]):>9s}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
