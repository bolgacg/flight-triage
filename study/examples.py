#!/usr/bin/env python3
"""Downsampled time series for the handful of flights the page walks through.

The study only keeps one number per indicator per flight. The page's first act
shows what those numbers were computed from, so this script re-downloads a few
named logs and writes a second-by-second series for each.

Usage:
    python3 study/examples.py <log_id> [<log_id> ...]
    python3 study/examples.py --auto     # choose from data/results.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import TOPICS, Log, hf_energy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def per_second(t_us: np.ndarray, v: np.ndarray, fn) -> tuple[list[float], list[float]]:
    ok = np.isfinite(t_us) & np.isfinite(v)
    t_us, v = t_us[ok], v[ok]
    if t_us.size < 5:
        return [], []
    order = np.argsort(t_us)
    t_us, v = t_us[order], v[order]
    secs = ((t_us - t_us[0]) / 1e6).astype(int)
    xs, ys = [], []
    for b in np.unique(secs):
        seg = v[secs == b]
        if seg.size:
            xs.append(float(b))
            ys.append(round(float(fn(seg)), 4))
    return xs, ys


def series_for(log_id: str, meta: dict) -> dict:
    from pyulog import ULog

    url = meta.get("download_url") or f"https://cdn.logs.px4.io/{log_id}.ulg"
    req = urllib.request.Request(
        url, headers={"User-Agent": "flight-triage study (github.com/bolgacg/flight-triage)"}
    )
    payload = urllib.request.urlopen(req, timeout=180).read()
    fd, tmp = tempfile.mkstemp(suffix=".ulg")
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    try:
        ulog = ULog(tmp, message_name_filter_list=TOPICS)
        lg = Log(ulog)
    finally:
        os.unlink(tmp)

    out: dict = {"log_id": log_id, "armed_s": round(lg.armed_seconds(), 1), "series": {}}

    # What the autopilot itself said during the flight. pyulog parses these
    # string messages whatever topic filter is set, so they cost nothing. A
    # message ending in a tab is PX4's duplicate of a newer typed event and is
    # dropped, the way pyulog's own message tool drops it.
    said = []
    for m in getattr(ulog, "logged_messages", []) or []:
        text = (m.message or "").strip()
        if not text or (m.message or "").endswith("\t"):
            continue
        t = (m.timestamp - lg.t0) / 1e6
        if t < -5 or t > lg.armed_seconds() + 30:
            continue
        try:
            level = m.log_level_str()
        except Exception:  # noqa: BLE001
            level = str(getattr(m, "log_level", ""))
        said.append({"t": round(float(t), 1), "level": level, "text": text[:160]})
    said.sort(key=lambda r: r["t"])
    out["said"] = said[:40]
    out["said_total"] = len(said)

    ax = lg.field("sensor_combined", "accelerometer_m_s2[0]")
    ay = lg.field("sensor_combined", "accelerometer_m_s2[1]")
    az = lg.field("sensor_combined", "accelerometer_m_s2[2]")
    if ax[1] is not None and ay[1] is not None and az[1] is not None:
        n = min(ax[1].size, ay[1].size, az[1].size)
        t = ax[0][:n]
        mag = np.sqrt(ax[1][:n] ** 2 + ay[1][:n] ** 2 + az[1][:n] ** 2)
        order = np.argsort(t)
        t, mag = t[order], mag[order]
        dt = float(np.median(np.diff(t)) / 1e6) if t.size > 2 else 0.0
        if dt > 0:
            w = max(3, int(round(0.5 / dt)))
            smooth = np.convolve(mag, np.ones(w) / w, mode="same")
            hf = mag - smooth
            e = w // 2 + 1
            if hf.size > 2 * e:
                xs, ys = per_second(t[e:-e], hf[e:-e], np.std)
                out["series"]["vibration"] = {"x": xs, "y": ys, "unit": "m/s²", "label": "Vibration"}

    ratios = []
    for name in ("mag_test_ratio", "pos_test_ratio", "hgt_test_ratio", "hagl_test_ratio", "beta_test_ratio"):
        r = lg.field("estimator_status", name)
        if r[1] is not None and r[1].size:
            ratios.append(r)
    if ratios:
        n = min(a[1].size for a in ratios)
        t = ratios[0][0][:n]
        worst = np.nanmax(np.vstack([a[1][:n] for a in ratios]), axis=0)
        xs, ys = per_second(t, worst, np.max)
        out["series"]["estimator"] = {"x": xs, "y": ys, "unit": "ratio", "label": "Worst estimator test ratio"}

    ts, sp_r = lg.field("vehicle_rates_setpoint", "roll")
    _, sp_p = lg.field("vehicle_rates_setpoint", "pitch")
    _, sp_y = lg.field("vehicle_rates_setpoint", "yaw")
    ta, av_x = lg.field("vehicle_angular_velocity", "xyz[0]")
    _, av_y = lg.field("vehicle_angular_velocity", "xyz[1]")
    _, av_z = lg.field("vehicle_angular_velocity", "xyz[2]")
    if av_x is None:
        ta, av_x = lg.field("vehicle_attitude", "rollspeed")
        _, av_y = lg.field("vehicle_attitude", "pitchspeed")
        _, av_z = lg.field("vehicle_attitude", "yawspeed")
    if all(a is not None and a.size > 2 for a in (ts, sp_r, sp_p, sp_y, ta, av_x, av_y, av_z)):
        o = np.argsort(ta)
        comps = []
        for sp, av in ((sp_r, av_x), (sp_p, av_y), (sp_y, av_z)):
            comps.append(sp - np.interp(ts, ta[o], av[o]))
        err = np.sqrt(sum(c * c for c in comps))
        xs, ys = per_second(ts, err, lambda s: np.percentile(s, 95))
        out["series"]["tracking"] = {"x": xs, "y": ys, "unit": "rad/s", "label": "Rate tracking error"}

    tv, volt = lg.field("battery_status", "voltage_filtered_v")
    if volt is None:
        tv, volt = lg.field("battery_status", "voltage_v")
    if volt is not None and volt.size:
        xs, ys = per_second(tv, volt, np.min)
        out["series"]["battery"] = {"x": xs, "y": ys, "unit": "V", "label": "Pack voltage"}

    for k in ("group", "rating", "mav_type", "airframe_name", "log_date", "ver_sw_release", "error_label_names"):
        out[k] = meta.get(k)
    out["review_url"] = f"https://review.px4.io/plot_app?log={log_id}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    cohort = {f["log_id"]: f for f in json.loads((DATA / "cohort.json").read_text())["flights"]}
    ids = list(args.ids)
    if args.auto:
        res = json.loads((DATA / "results.json").read_text())
        thr = res["combined"]["thresholds"]
        names = [n for n, t in thr.items() if t is not None]

        def hits(item):
            n = 0
            for name in names:
                v, t = item.get(name), thr[name]
                if v is None or t is None:
                    continue
                if (v > t) if name != "batt_min_cell_v" else (v < t):
                    n += 1
            return n

        q = res["queue"]
        cases = sorted([i for i in q if i["group"] == "case"], key=hits)
        controls = sorted([i for i in q if i["group"] == "control"], key=hits)
        picks = []
        if cases:
            picks.append(cases[-1]["log_id"])  # the loudest crash
            picks.append(cases[0]["log_id"])  # the crash the rule misses
        if controls:
            picks.append(controls[-1]["log_id"])  # the healthy flight it flags
            picks.append(controls[0]["log_id"])  # a quiet flight
        ids = [i for i in picks if i]
        print("auto picks:", ids)

    out = []
    for lid in ids:
        meta = cohort.get(lid, {"log_id": lid})
        print("fetching", lid, flush=True)
        try:
            out.append(series_for(lid, meta))
        except Exception as exc:  # noqa: BLE001
            print("  failed:", exc)
    (DATA / "examples.json").write_text(json.dumps(out))
    print(f"wrote data/examples.json with {len(out)} flights")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
