# Preregistration

Written 14 September 2026 by Bolgaç Gülen, before any group was compared and
before `study/score.py` existed. The cohort and the indicators were fixed first;
this file records what will count as an answer, so the answer cannot be chosen
after the fact.

## The question

A flight log records far more than whether the aircraft came home. The question
is whether the log of a flight, read by a fixed rule, says that the aircraft was
in trouble, on flights where a human later recorded that it crashed for a
hardware or software reason.

The practical version, for a centre that flies many platforms: if a week of logs
arrives, can a fixed rule put the ones worth a human's attention at the top,
without burying the reader in false alarms?

## The corpus

The public PX4 log database, read through its own index at
`https://review.px4.io/dbinfo` (a gzipped JSON rebuilt daily, 463,000 public
records on 14 September 2026). Each record carries the rating the uploader chose
and any error labels a reviewer attached. Those ratings are the only human
judgement used here. Every other number is computed from the log.

## Groups

Fixed in `study/cohort.py` with seed 20260914, before any log was parsed:

| Group | Rule | Role |
|---|---|---|
| case | `rating == 'crash_sw_hw'` | the aircraft or its software failed |
| control | `rating in {'good','great'}` | flights the pilot was happy with |
| pilot | `rating == 'crash_pilot'` | falsification group, see below |
| poor | `rating == 'unsatisfactory'` | reported, not part of the primary result |
| labelled | any reviewer error label | secondary check only |

Filters applied to every group: vehicle type in {Quadrotor, Hexarotor,
Octorotor, Fixed Wing, VTOL Standard}; logged duration between 20 and 600
seconds. Controls are matched to cases three per case on vehicle type and
duration band (20 to 60, 60 to 180, 180 to 600 seconds), drawn with the seed
above. Caps: 200 pilot, 250 poor, 300 labelled stratified across label types.

**The falsification group.** A crash the uploader blamed on the pilot is a
flight that ended badly with a machine that may have been healthy. If the
indicators separate pilot-error crashes from controls as strongly as they
separate hardware and software crashes from controls, then they are detecting
"a flight that ended badly" rather than "an aircraft in trouble". That result
would be reported as the headline, and the tool's claim would be withdrawn to
what the evidence supports.

## The split

`split = 'fit' if int(md5(log_id)[0], 16) % 2 == 0 else 'measure'`.

Nothing about a log's contents or its rating enters this. The **fit** half is
where every threshold is chosen. The **measure** half is read once, to report.
Every later read of the measure half will be listed at the bottom of this file,
with its reason.

## Indicators

Each is one number per flight, computed over the armed portion of the log only.
Higher means worse in every case except `batt_min_cell_v`, which is inverted in
scoring. Definitions are in `study/features.py` and were written before any
group comparison.

First tier, available across the whole decade of firmware in the cohort
(PX4 v1.6 to v1.16), computed from raw signals so a 2018 log and a 2026 log are
measured by the same rule:

1. `vib_hf_ms2` Vibration. The accelerometer magnitude minus its half-second
   rolling mean, reported as the 95th percentile of the per-second standard
   deviation of what is left, in metres per second squared.
2. `est_reject_frac` The fraction of the flight in which the position estimator
   rejected at least one of its measurements, meaning any of the magnetometer,
   position, height, height-above-ground or sideslip test ratios exceeded 1.0.
3. `est_ratio_p95` The 95th percentile of the worst of those test ratios.
4. `est_fault_frac` The fraction of the flight with a non-zero estimator fault
   flag.
5. `track_err_p95` Rate tracking error. The 95th percentile of the magnitude of
   the difference between the rates the controller asked for and the rates the
   airframe produced, in radians per second.
6. `batt_sag_ohm` Pack internal resistance, from a least squares fit of voltage
   on current across the flight.
7. `batt_min_cell_v` The lowest per-cell voltage seen in the upper half of the
   current draw. Inverted when scored.
8. `gps_bad_frac` The fraction of the flight without a three dimensional
   satellite fix.

Second tier, newer firmware only, reported on that subset with its own count:

9. `fd_any_frac` The fraction of the flight in which PX4's **own** failure
   detector raised any flag. This is the study's baseline: it is already running
   on these aircraft, so any first-tier indicator has to earn its place against
   it.
10. `vib_accel_p95` PX4's own vibration metric, used to check indicator 1.
11. `sat_frac`, `unalloc_torque_p95` Control allocation limits reached.
12. `accel_inconsistency_p95` Disagreement between redundant accelerometers.
13. `accel_clip_per_min` Accelerometer clipping events per armed minute.
14. `imbalanced_prop_p95` PX4's imbalanced propeller metric.

A free baseline needing no log parsing at all, taken from the index:
`num_logged_errors > 0`.

## The primary analysis

1. For each indicator, choose a threshold on the **fit** half so that it flags
   10 percent of control flights. Ten percent is the false-alarm budget, fixed
   here: one false alarm per ten healthy flights is the most a person triaging a
   week of logs will tolerate.
2. Report, on the **measure** half, the fraction of case flights that the
   indicator flags, with a 95 percent interval.
3. Report the same for the pilot group and the poor group.
4. Combined rule: flag a flight if any first-tier indicator exceeds its
   threshold, where all thresholds are set from a single control quantile `q`.
   `q` is chosen on the fit half as the value whose combined false-alarm rate is
   closest to 10 percent. That is one free parameter, chosen once, on the fit
   half.
5. Compare every result against the two baselines: PX4's own failure detector,
   and `num_logged_errors > 0`.

Missing values: an indicator that is null for a flight cannot flag it. Rates are
computed over the flights where the indicator exists, and the coverage of each
indicator is reported beside its result.

## The secondary check

The `labelled` group contains flights where a reviewer named the fault. For the
94 flights labelled Vibration, report where `vib_hf_ms2` places them in the
distribution of all cohort flights. An indicator that claims to measure
vibration should rank the flights a human called vibration above the rest. If it
does not, that is reported.

## What would make this a failure

- The pilot-error group is flagged as often as the hardware and software group.
- No first-tier indicator beats PX4's own failure detector at the same
  false-alarm rate, in which case the honest conclusion is that the autopilot
  already tells you what the log can.
- Coverage is so thin that a rate rests on too few flights to mean anything;
  any result on fewer than 30 flights will be marked as such and not used in a
  headline.

## Changes to this registration

**14 September 2026, before the measured half was read: repeat uploads removed.**
The same flight is sometimes uploaded to the public database more than once. Two
copies of one flight would be counted twice and could land in opposite halves of
the split, which is exactly what the split is there to prevent. Flights sharing a
vehicle identifier, a date and a duration are now treated as one, keeping the
first by log id. This removed 37 rows, most of them crashes, taking the cohort
from 1,679 to 1,642 flights. The rule is mechanical and uses nothing about the
indicators or the outcome.

**14 September 2026, before the measured half was read: a validity rule on cell
voltage.** Some packs report the wrong number of cells, which makes the lowest
per-cell voltage come out at values a lithium cell cannot have. Readings outside
2.0 to 4.6 volts are now treated as missing rather than as measurements. The rule
is applied to every group alike and uses nothing about the outcome. It removed
the reading on 39 flights and is reported in the indicator's coverage.

**14 September 2026, after the fit half was read: a second combining rule.**
The rule registered above flags a flight when any one of eight indicators goes
over its threshold. Holding the false alarm budget then forces every one of the
eight to a strict threshold, and on the fit half it performed worse than several
single indicators. A second rule was added: flag a flight when at least k of the
eight sit above a looser threshold, with both k and the threshold quantile chosen
on the fit half. It is not preregistered, it is labelled as exploratory wherever
it appears, and the registered rule is still reported beside it. The measured half
was not consulted in choosing it.

**14 September 2026, after the fit half was read: the whole study run again with
the ending of every flight removed.** Every flight in the cohort ends on the
ground, and an impact writes vibration and rate-tracking error into the log
whatever caused it. On the fit half the falsification group was being flagged
almost as often as the hardware and software group, which is exactly what that
confound would look like. So the same indicators are computed a second time over
the armed window minus its last 10 seconds, and both versions are reported side
by side. This is an added analysis, not a replacement: the preregistered numbers
stand as they are. The 10 seconds was chosen once, before the second pass ran,
and no other value was tried.

## Reads of the measure half

1. **14 September 2026, the planned read.** `study/score.py` run once on all 1,642
   flights, after every threshold rule was fixed. Result: the registered rule
   flags 56 percent of hardware and software crashes at 8 percent of healthy
   flights, against 32 percent for PX4's own failure detector and 38 percent at
   23 percent false alarms for the free "the autopilot wrote an error" rule. The
   exploratory agreement rule reached 54 percent at 10 percent, which is not an
   improvement, so the registered rule is the one reported. Pilot-error crashes
   are flagged at 28 percent, between the crash and healthy groups.
2. **14 September 2026, the tail-cut pass.** The same read repeated on the second
   pass, with the last 10 seconds of every flight removed and flights left with
   under 15 seconds dropped. Result: the registered rule flags 37 percent of
   hardware and software crashes, 18 percent of pilot-error crashes and 8 percent
   of healthy flights, against 15 percent for PX4's own failure detector. So
   about two thirds of what the rule found on the whole flight survives with the
   ending taken away, the false-alarm rate does not move, and the gap between the
   two crash groups is still a factor of two. That is the evidence that the
   indicators are reading the aircraft rather than the impact, and it is the
   answer to the falsification test set out above.
