# Which of these flights was already in trouble?

A preregistered study of public PX4 flight logs, asking whether a fixed rule reading the
log alone can find the flights that crashed for a hardware or software reason, without
burying the reader in false alarms.

Live page: https://bolgacg.github.io/flight-triage/

Built for the engineer assistant position at SDU Drone Center (job 4209), around a gap the
centre published about itself: in the compliance table of its 2026 Kenya operations paper,
the safety objective on maintenance is marked not met, with the evaluation "No formal
maintenance structure exists. There are no documented maintenance instructions, no
dedicated maintenance personnel, and no maintenance log or record-keeping system."
The same campaign flew more than 260 flights and listed post-flight logging as one of the
few steps applied consistently. The logs exist; nothing reads them.

## What is here

| Path | What it does |
|---|---|
| `study/cohort.py` | Builds the flight cohort from the public log index at review.px4.io/dbinfo |
| `study/features.py` | Downloads each log, computes one number per indicator, deletes the log |
| `study/score.py` | Chooses thresholds on the fit half, measures them once on the other |
| `study/examples.py` | Second-by-second series and the autopilot's own messages, for the flights the page walks through |
| `study/build_page_data.py` | Writes `docs/data.js`, the only thing the page reads |
| `study/verify_page.js` | Renders the page headless, screenshots it, walks every tour step, checks for overflow and script errors |
| `PREREGISTRATION.md` | What was fixed before the data was read, and every change since |
| `docs/` | The page |

## Running it

```
pip install pyulog numpy
python3 study/cohort.py          # writes data/cohort.json
python3 study/features.py        # a few hours; resumable, appends to data/features.jsonl
python3 study/score.py           # writes data/results.json
python3 study/examples.py --auto # writes data/examples.json
python3 study/build_page_data.py # writes docs/data.js
node study/verify_page.js        # renders, screenshots and walks the page
```

`study/features.py` can be stopped and restarted; it skips logs it has already processed.
Raw logs are never kept, so the disk cost stays flat no matter how many flights are read.

## The short version of the method

Flights are grouped by the rating their pilot chose when uploading: a crash blamed on
hardware or software, a flight rated good or great, a crash the pilot blamed on themselves,
or a flight rated unsatisfactory. Controls are matched to crashes three to one on vehicle
type and flight length. Each flight is assigned to one of two halves by a hash of its log
id; thresholds are chosen on one half and reported on the other.

Crashes the pilot blamed on themselves are the falsification group. A rule that flags those
as often as hardware failures is reading bad endings rather than bad aircraft, and the page
would have said so.

## Sources

- Public log index: https://review.px4.io/dbinfo, logs from `cdn.logs.px4.io`
- Parsing: https://github.com/PX4/pyulog
- Maalouf and others including Schultz Lundquist, "SORA 2.5-Guided BVLOS UAS for Wildlife
  Conservation in Kenya", Drones 2026, 10(3), 178
- Schultz Lundquist and others, "WildDrone: autonomous drone technology for monitoring
  wildlife populations", Frontiers in Robotics and AI 12, 2026
- Job advertisement 4209, SDU Drone Center, read 20 August 2026

Bolgaç Gülen, September 2026.
