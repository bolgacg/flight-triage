#!/usr/bin/env python3
"""Download each log in the cohort, compute the indicators, throw the log away.

One row per flight, written to data/features.jsonl as it goes so the run can be
stopped and resumed. Raw logs are deleted immediately after parsing; only the
numbers survive.

Every indicator is a single number per flight, defined here and in
PREREGISTRATION.md before any of them was looked at. Higher always means worse.
Where a topic is missing from a log the indicator is null, and the coverage is
reported on the page rather than filled in.

Usage:
    python3 study/features.py            # process what is missing
    python3 study/features.py --workers 6
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import traceback
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "features.jsonl"

TOPICS = [
    # present across the whole decade of firmware in this cohort
    "vehicle_status",
    "actuator_armed",
    "estimator_status",
    "vehicle_rates_setpoint",
    "vehicle_attitude",
    "sensor_combined",
    "battery_status",
    "vehicle_local_position",
    "vehicle_gps_position",
    # newer firmware only; these make up the second tier
    "vehicle_imu_status",
    "sensors_status_imu",
    "failure_detector_status",
    "control_allocator_status",
    "vehicle_angular_velocity",
]

ARMING_STATE_ARMED = 2


def _p(x: np.ndarray, q: float) -> float | None:
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    return float(np.percentile(x, q))


def _frac(mask: np.ndarray) -> float | None:
    if mask.size == 0:
        return None
    return float(np.count_nonzero(mask) / mask.size)


class Log:
    """Thin accessor over a parsed ULog, tolerant of missing topics.

    `tail_cut` removes that many seconds from the end of the armed window. Every
    flight in this study ends on the ground one way or another, and an impact
    writes vibration and tracking error into the log whatever caused it. Cutting
    the tail asks the sharper question: was the aircraft already in trouble
    before the ending?
    """

    def __init__(self, ulog, tail_cut: float = 0.0):
        self.u = ulog
        self.sets: dict[str, list] = {}
        for d in ulog.data_list:
            self.sets.setdefault(d.name, []).append(d)
        self.t0, self.t1 = self._armed_window()
        self.tail_cut = tail_cut
        if tail_cut > 0 and self.t1 > self.t0:
            self.t1 = max(self.t0, self.t1 - tail_cut * 1e6)

    def _armed_window(self) -> tuple[float, float]:
        for name, field, want in (
            ("vehicle_status", "arming_state", ARMING_STATE_ARMED),
            ("actuator_armed", "armed", 1),
        ):
            for d in self.sets.get(name, []):
                if field not in d.data:
                    continue
                t = np.asarray(d.data["timestamp"], dtype=float)
                v = np.asarray(d.data[field])
                armed = v == want
                if np.any(armed):
                    return float(t[armed][0]), float(t[armed][-1])
        # No arming information: use the whole log.
        best = (math.inf, -math.inf)
        for sets in self.sets.values():
            for d in sets:
                t = np.asarray(d.data.get("timestamp", []), dtype=float)
                if t.size:
                    best = (min(best[0], float(t[0])), max(best[1], float(t[-1])))
        return best if best[0] < best[1] else (0.0, 0.0)

    def armed_seconds(self) -> float:
        return max(0.0, (self.t1 - self.t0) / 1e6)

    def field(self, topic: str, field: str, instance: int | None = None):
        """Return (time, value) inside the armed window, concatenated over
        instances unless one is named. Missing topic or field gives None."""
        out_t, out_v = [], []
        for d in self.sets.get(topic, []):
            if instance is not None and d.multi_id != instance:
                continue
            if field not in d.data or "timestamp" not in d.data:
                continue
            t = np.asarray(d.data["timestamp"], dtype=float)
            v = np.asarray(d.data[field], dtype=float)
            m = (t >= self.t0) & (t <= self.t1)
            if np.any(m):
                out_t.append(t[m])
                out_v.append(v[m])
        if not out_t:
            return None, None
        return np.concatenate(out_t), np.concatenate(out_v)

    def instances(self, topic: str) -> list[int]:
        return [d.multi_id for d in self.sets.get(topic, [])]

    def max_over_instances(self, topic: str, field: str):
        """Per-instance arrays stacked by taking the elementwise max after
        aligning on each instance's own samples. Returns one pooled array."""
        vals = []
        for inst in self.instances(topic):
            _, v = self.field(topic, field, inst)
            if v is not None and v.size:
                vals.append(v)
        if not vals:
            return None
        return np.concatenate(vals)


def hf_energy(t_us: np.ndarray, mag: np.ndarray, window_s: float = 0.5) -> float | None:
    """Vibration, computed the same way on every firmware in the cohort.

    Take the magnitude of the accelerometer vector, subtract a half-second
    rolling mean so that gravity and real manoeuvres drop out, then report the
    95th percentile of the per-second standard deviation of what is left. The
    unit is metres per second squared, and a clean airframe sits near 1 while a
    loose motor mount runs far higher. This is the same idea as PX4's own
    vibration metric, which only newer firmware logs; the two are compared on
    the flights that carry both.
    """
    ok = np.isfinite(t_us) & np.isfinite(mag)
    t_us, mag = t_us[ok], mag[ok]
    if t_us.size < 200:
        return None
    order = np.argsort(t_us)
    t_us, mag = t_us[order], mag[order]
    dt = np.median(np.diff(t_us)) / 1e6
    if not np.isfinite(dt) or dt <= 0:
        return None
    n = max(3, int(round(window_s / dt)))
    if n >= mag.size:
        return None
    kernel = np.ones(n) / n
    smooth = np.convolve(mag, kernel, mode="same")
    hf = mag - smooth
    edge = n // 2 + 1
    if hf.size <= 2 * edge:
        return None
    hf = hf[edge:-edge]
    ts = (t_us[edge:-edge] - t_us[edge]) / 1e6
    bins = np.floor(ts).astype(int)
    stds = []
    for b in np.unique(bins):
        seg = hf[bins == b]
        if seg.size > 10:
            stds.append(float(np.std(seg)))
    if len(stds) < 3:
        return None
    return float(np.percentile(stds, 95))


def indicators(lg: Log) -> dict:
    """Every number the study uses. Higher means worse, except where the name
    says otherwise (batt_min_cell_v), which score.py inverts."""
    f: dict = {}
    secs = lg.armed_seconds()
    f["armed_s"] = round(secs, 2)

    # 1. Vibration, computed here from the raw accelerometer so that a 2018 log
    # and a 2026 log are measured by the same rule.
    ax = lg.field("sensor_combined", "accelerometer_m_s2[0]")
    ay = lg.field("sensor_combined", "accelerometer_m_s2[1]")
    az = lg.field("sensor_combined", "accelerometer_m_s2[2]")
    if ax[1] is not None and ay[1] is not None and az[1] is not None:
        n = min(ax[1].size, ay[1].size, az[1].size)
        mag = np.sqrt(ax[1][:n] ** 2 + ay[1][:n] ** 2 + az[1][:n] ** 2)
        f["vib_hf_ms2"] = hf_energy(ax[0][:n], mag)
    else:
        f["vib_hf_ms2"] = None

    # PX4's own metric, newer firmware only, kept to check the one above.
    v = lg.max_over_instances("vehicle_imu_status", "accel_vibration_metric")
    f["vib_accel_p95"] = _p(v, 95) if v is not None else None
    g = lg.max_over_instances("vehicle_imu_status", "gyro_vibration_metric")
    f["vib_gyro_p95"] = _p(g, 95) if g is not None else None

    # 2. Accelerometer clipping, counted per armed minute. The counters are
    # cumulative, so the rise across the flight is what matters.
    rises = []
    for inst in lg.instances("vehicle_imu_status"):
        total = 0.0
        for axis in range(3):
            _, c = lg.field("vehicle_imu_status", f"accel_clipping[{axis}]", inst)
            if c is not None and c.size >= 2:
                total += max(0.0, float(c[-1] - c[0]))
        rises.append(total)
    f["accel_clip_per_min"] = (max(rises) / (secs / 60)) if rises and secs > 0 else None

    # 3. Estimator rejections. A test ratio above 1.0 is the EKF saying it does
    # not believe that measurement.
    ratios = []
    for name in ("mag_test_ratio", "pos_test_ratio", "hgt_test_ratio", "tas_test_ratio", "hagl_test_ratio", "beta_test_ratio"):
        r = lg.max_over_instances("estimator_status", name)
        if r is not None and r.size:
            ratios.append(r)
    if ratios:
        n = min(a.size for a in ratios)
        stacked = np.vstack([a[:n] for a in ratios])
        worst = np.nanmax(stacked, axis=0)
        f["est_reject_frac"] = _frac(worst > 1.0)
        f["est_ratio_p95"] = _p(worst, 95)
    else:
        f["est_reject_frac"] = None
        f["est_ratio_p95"] = None

    ff = lg.max_over_instances("estimator_status", "filter_fault_flags")
    f["est_fault_frac"] = _frac(ff != 0) if ff is not None else None

    # 4. Rate tracking error: what the controller asked for against what the
    # airframe did, in radians per second.
    err = None
    ts, sp_r = lg.field("vehicle_rates_setpoint", "roll")
    _, sp_p = lg.field("vehicle_rates_setpoint", "pitch")
    _, sp_y = lg.field("vehicle_rates_setpoint", "yaw")
    ta, av_x = lg.field("vehicle_angular_velocity", "xyz[0]")
    _, av_y = lg.field("vehicle_angular_velocity", "xyz[1]")
    _, av_z = lg.field("vehicle_angular_velocity", "xyz[2]")
    if av_x is None:
        # Firmware before the angular_velocity topic logs the same rates on
        # vehicle_attitude.
        ta, av_x = lg.field("vehicle_attitude", "rollspeed")
        _, av_y = lg.field("vehicle_attitude", "pitchspeed")
        _, av_z = lg.field("vehicle_attitude", "yawspeed")
    if all(a is not None and a.size > 2 for a in (ts, sp_r, sp_p, sp_y, ta, av_x, av_y, av_z)):
        order = np.argsort(ta)
        ta_s = ta[order]
        comps = []
        for sp, av in ((sp_r, av_x), (sp_p, av_y), (sp_y, av_z)):
            got = np.interp(ts, ta_s, av[order])
            comps.append(sp - got)
        err = np.sqrt(sum(c * c for c in comps))
    f["track_err_p95"] = _p(err, 95) if err is not None else None

    # 5. Control allocation: motors pinned at a limit, and torque the mixer
    # could not deliver.
    sat_any = None
    for i in range(16):
        _, s = lg.field("control_allocator_status", f"actuator_saturation[{i}]")
        if s is None or s.size == 0:
            continue
        m = s != 0
        sat_any = m if sat_any is None else (sat_any[: m.size] | m[: sat_any.size])
    f["sat_frac"] = _frac(sat_any) if sat_any is not None else None

    ut = []
    for i in range(3):
        _, u = lg.field("control_allocator_status", f"unallocated_torque[{i}]")
        if u is not None and u.size:
            ut.append(u)
    if ut:
        n = min(a.size for a in ut)
        f["unalloc_torque_p95"] = _p(np.sqrt(sum(a[:n] ** 2 for a in ut)), 95)
    else:
        f["unalloc_torque_p95"] = None

    # 6. Battery: internal resistance from a least-squares fit of voltage on
    # current, and the lowest per-cell voltage seen under load.
    _, volt = lg.field("battery_status", "voltage_filtered_v")
    if volt is None:
        _, volt = lg.field("battery_status", "voltage_v")
    _, cur = lg.field("battery_status", "current_filtered_a")
    if cur is None:
        _, cur = lg.field("battery_status", "current_a")
    _, cells = lg.field("battery_status", "cell_count")
    f["batt_sag_ohm"] = None
    f["batt_min_cell_v"] = None
    if volt is not None and cur is not None and volt.size > 20 and cur.size > 20:
        n = min(volt.size, cur.size)
        vv, ii = volt[:n], cur[:n]
        ok = np.isfinite(vv) & np.isfinite(ii) & (vv > 1.0)
        if np.count_nonzero(ok) > 20 and float(np.ptp(ii[ok])) > 1.0:
            A = np.vstack([np.ones(np.count_nonzero(ok)), ii[ok]]).T
            try:
                coef, *_ = np.linalg.lstsq(A, vv[ok], rcond=None)
                f["batt_sag_ohm"] = float(-coef[1])
            except np.linalg.LinAlgError:
                pass
        ncell = None
        if cells is not None and cells.size:
            c = cells[np.isfinite(cells) & (cells > 0)]
            if c.size:
                ncell = float(np.median(c))
        if ncell:
            under_load = vv[ok][ii[ok] > np.percentile(ii[ok], 50)] if np.count_nonzero(ok) > 20 else vv[ok]
            if under_load.size:
                f["batt_min_cell_v"] = float(np.min(under_load) / ncell)

    # 7. Redundant-sensor disagreement.
    inc = []
    for i in range(4):
        _, a = lg.field("sensors_status_imu", f"accel_inconsistency_m_s_s[{i}]")
        if a is not None and a.size:
            inc.append(a)
    f["accel_inconsistency_p95"] = _p(np.concatenate(inc), 95) if inc else None

    # 8. The onboard baseline: PX4's own failure detector, which is already
    # running on every one of these aircraft.
    fd_fields = ("fd_roll", "fd_pitch", "fd_alt", "fd_ext", "fd_arm_escs", "fd_battery", "fd_imbalanced_prop", "fd_motor")
    fd_any = None
    fd_which = []
    for name in fd_fields:
        _, a = lg.field("failure_detector_status", name)
        if a is None or a.size == 0:
            continue
        m = a != 0
        if np.any(m):
            fd_which.append(name)
        fd_any = m if fd_any is None else (fd_any[: m.size] | m[: fd_any.size])
    f["fd_any_frac"] = _frac(fd_any) if fd_any is not None else None
    f["fd_flags"] = fd_which
    _, ip = lg.field("failure_detector_status", "imbalanced_prop_metric")
    f["imbalanced_prop_p95"] = _p(ip, 95) if ip is not None else None

    # 9. Satellite navigation: time spent without a three dimensional fix.
    _, fix = lg.field("vehicle_gps_position", "fix_type")
    f["gps_bad_frac"] = _frac(fix < 3) if fix is not None and fix.size else None
    _, sats = lg.field("vehicle_gps_position", "satellites_used")
    f["gps_sats_min"] = float(np.min(sats)) if sats is not None and sats.size else None

    # 10. Descent rate, to describe what the end of the flight looked like.
    _, vz = lg.field("vehicle_local_position", "vz")
    f["descent_p99"] = _p(vz, 99) if vz is not None else None

    # Which tier of firmware this log belongs to. The second tier carries the
    # autopilot's own failure detector, which is the study's baseline.
    f["tier"] = "modern" if "failure_detector_status" in lg.sets else "legacy"
    return f


def process(row: dict, keep_dir: str | None = None, tail_cut: float = 0.0) -> dict:
    from pyulog import ULog

    log_id = row["log_id"]
    url = row.get("download_url") or f"https://cdn.logs.px4.io/{log_id}.ulg"
    rec = {"log_id": log_id, "ok": False}
    tmp = None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "flight-triage study (github.com/bolgacg/flight-triage)"}
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = r.read()
        rec["bytes"] = len(payload)
        fd, tmp = tempfile.mkstemp(suffix=".ulg")
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        ulog = ULog(tmp, message_name_filter_list=TOPICS)
        lg = Log(ulog, tail_cut=tail_cut)
        rec.update(indicators(lg))
        if tail_cut:
            rec["tail_cut_s"] = tail_cut
        rec["topics_present"] = sorted(lg.sets.keys())
        rec["dropouts"] = len(getattr(ulog, "dropouts", []) or [])
        rec["ok"] = True
        if keep_dir:
            Path(keep_dir).mkdir(parents=True, exist_ok=True)
            Path(keep_dir, f"{log_id}.ulg").write_bytes(payload)
    except Exception as exc:  # noqa: BLE001 - one bad log must not stop the run
        rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["trace"] = traceback.format_exc(limit=3)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keep-dir", default="")
    ap.add_argument("--only", default="", help="comma separated log ids")
    ap.add_argument("--tail-cut", type=float, default=0.0,
                    help="seconds to drop from the end of the armed window; writes a separate file")
    args = ap.parse_args()

    out_path = OUT if not args.tail_cut else DATA / f"features_cut{int(args.tail_cut)}.jsonl"

    cohort = json.loads((DATA / "cohort.json").read_text())["flights"]
    done: set[str] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["log_id"])
                except json.JSONDecodeError:
                    pass
    if args.only:
        wanted = set(args.only.split(","))
        todo = [r for r in cohort if r["log_id"] in wanted]
    else:
        todo = [r for r in cohort if r["log_id"] not in done]
    # Interleave the groups with a fixed shuffle so a partial run is still
    # balanced across case, control, pilot and poor.
    import random as _random

    _random.Random(20260914).shuffle(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"cohort {len(cohort)}, already done {len(done)}, to process {len(todo)}")
    if not todo:
        return 0

    n_ok = n_err = 0
    with open(out_path, "a") as out, ProcessPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(process, r, args.keep_dir or None, args.tail_cut): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            out.write(json.dumps(rec) + "\n")
            out.flush()
            if rec.get("ok"):
                n_ok += 1
            else:
                n_err += 1
            if i % 25 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}  ok={n_ok} failed={n_err}", flush=True)
    print(f"done: {n_ok} parsed, {n_err} failed -> {out_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
