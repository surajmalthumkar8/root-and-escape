"""Seeded synthetic plant data for Root & Escape.

Everything here is SYNTHETIC. It imitates an injection-moulding plant that makes the CL-204
snap clip on six presses, each running a four-cavity mould, three shifts a day.

Three investigation cases are planted on purpose. Each one has a truth record in the case catalog.

Case 118, crack:
- Occurrence: the press-4 resin dryer fails (dew point about -18 C instead of -45 C) from
  5 Oct 06:00 to 12 Oct 14:00. Wet resin cracks.
- Escape: press-4 inspection sampling was cut from 5/h to 1/h on 2 Oct (control plan rev C).
- Confounder: from 1 Oct, new crew B runs press 4 on swing and night shifts and on alternate
  day shifts. A naive analysis therefore blames crew B, but inside press 4 every crew is
  equally bad.

Case 124, burr:
- Occurrence: the press-2 mould skipped its preventive maintenance. Cavity C3 wears past the
  250k-shot limit from 28 Sep onward and makes burrs.
- Escape: the press-2 burr gauge G-02 was overdue for calibration from 27 Sep.

Case 121, scratch:
- No planted cause. This is the control case, where the honest answer is
  "insufficient evidence".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

START = date(2026, 8, 23)
END = date(2026, 10, 16)  # last production day (inclusive)
TODAY = date(2026, 10, 17)  # the demo's "today"
PART_NO = "CL-204"
PRESSES = ["P1", "P2", "P3", "P4", "P5", "P6"]
CAVITIES = ["C1", "C2", "C3", "C4"]
SHIFTS = ["D", "S", "N"]  # day 06-14, swing 14-22, night 22-06
SHIFT_START_HOUR = {"D": 6, "S": 14, "N": 22}
DEFECT_TYPES = ["crack", "burr", "scratch", "short_shot"]
BASE_RATE = {"crack": 0.003, "burr": 0.002, "scratch": 0.004, "short_shot": 0.002}
RESIN_LOTS = [  # (lot, first day in use) - resin lots change plant-wide every two weeks
    ("R-2288", date(2026, 8, 23)),
    ("R-2289", date(2026, 9, 7)),
    ("R-2290", date(2026, 9, 21)),
    ("R-2291", date(2026, 10, 5)),
]
RESIN_SUPPLIER = {"R-2288": "S1", "R-2289": "S2", "R-2290": "S1", "R-2291": "S2"}

# planted causes
DRYER_START = datetime(2026, 10, 5, 6)
DRYER_END = datetime(2026, 10, 12, 14)
CRACK_RATE_WET = 0.04
P4_SAMPLING_CUT = date(2026, 10, 2)
CREW_B_TAKES_P4 = date(2026, 10, 1)
BURR_START = date(2026, 9, 28)
BURR_RATE_WORN = 0.03
G02_CAL_DUE = date(2026, 9, 27)
PM_LIMIT_SHOTS = 250_000
SHOTS_PER_SHIFT = 1_800


def resin_for(day: date) -> str:
    lot = RESIN_LOTS[0][0]
    for name, first in RESIN_LOTS:
        if day >= first:
            lot = name
    return lot


def crew_for(press: str, shift: str, day: date) -> str:
    """Normal roster: A on days, C on swing, B on nights.
    From 1 Oct, crew B also takes press 4 swing and alternate press 4 day shifts."""
    if press == "P4" and day >= CREW_B_TAKES_P4:
        if shift in ("S", "N"):
            return "B"
        return "B" if day.toordinal() % 2 == 0 else "A"
    return {"D": "A", "S": "C", "N": "B"}[shift]


def shift_start(day: date, shift: str) -> datetime:
    return datetime(day.year, day.month, day.day, SHIFT_START_HOUR[shift])


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def lot_id(day: date, press: str, shift: str) -> str:
    return f"L{day:%m%d}-{press}-{shift}"


@dataclass
class Planted:
    """Optional extra planted segment, used by the benchmark generator."""
    defect: str
    filters: dict  # e.g. {"press": "P3"} or {"press": "P5", "cavity": "C2"}
    start: date
    end: date
    rate: float


@dataclass
class PlantData:
    production: pd.DataFrame
    process: pd.DataFrame
    inspection: pd.DataFrame
    shipments: pd.DataFrame
    past_8d: pd.DataFrame
    meta: dict = field(default_factory=dict)

    def tables(self) -> dict[str, pd.DataFrame]:
        return {
            "production_lots": self.production,
            "process_params": self.process,
            "inspection_log": self.inspection,
            "shipments": self.shipments,
            "past_8d": self.past_8d,
        }


def generate(seed: int = 42, planted_story: bool = True, extra: list[Planted] | None = None) -> PlantData:
    """Build all five tables. With planted_story=False only baseline noise (+ `extra`) is planted."""
    rng = np.random.default_rng(seed)
    extra = extra or []
    prod_rows, proc_rows, insp_rows, ship_rows = [], [], [], []

    # mould shot counters since last preventive maintenance (P2 is the one that skipped PM)
    shots = {p: int(rng.integers(20_000, 120_000)) for p in PRESSES}
    shots["P2"] = 56_000

    for day in _days(START, END):
        resin = resin_for(day)
        for press in PRESSES:
            for shift in SHIFTS:
                t0 = shift_start(day, shift)
                crew = crew_for(press, shift, day)
                wet = planted_story and press == "P4" and DRYER_START <= t0 < DRYER_END
                shots[press] += SHOTS_PER_SHIFT
                if press != "P2" and shots[press] >= PM_LIMIT_SHOTS - 10_000:
                    shots[press] = int(rng.integers(0, 5_000))  # maintained on time
                lid = lot_id(day, press, shift)
                for cav in CAVITIES:
                    units = int(max(100, rng.normal(150, 8)))
                    rec = {
                        "lot_id": lid, "part_no": PART_NO, "prod_date": day, "prod_ts": t0,
                        "press": press, "cavity": cav, "shift": shift, "crew": crew,
                        "resin_lot": resin, "resin_supplier": RESIN_SUPPLIER[resin], "units": units,
                    }
                    for d in DEFECT_TYPES:
                        rate = BASE_RATE[d]
                        if planted_story and d == "crack" and wet:
                            rate = CRACK_RATE_WET
                        if planted_story and d == "burr" and press == "P2" and cav == "C3" and day >= BURR_START:
                            rate = BURR_RATE_WORN
                        for pl in extra:
                            if pl.defect == d and pl.start <= day <= pl.end and all(rec.get(k) == v for k, v in pl.filters.items()):
                                rate = pl.rate
                        rec[f"defects_{d}"] = int(rng.binomial(units, rate))
                    prod_rows.append(rec)

                proc_rows.append({
                    "lot_id": lid, "prod_ts": t0, "press": press,
                    "melt_temp_c": round(float(rng.normal(230, 1.5)), 1),
                    "hold_pressure_bar": round(float(rng.normal(62, 1.0)), 1),
                    "cycle_time_s": round(float(rng.normal(28.0, 0.3)), 2),
                    "dryer_dew_point_c": round(float(rng.normal(-18, 2.0) if wet else rng.normal(-45, 2.0)), 1),
                    "mold_shots_since_pm": shots[press],
                })

                ship_day = day + timedelta(days=2)
                age = (TODAY - day).days
                status = "in_plant" if age < 2 else ("in_transit" if age <= 5 else "at_customer")
                ship_rows.append({
                    "lot_id": lid, "prod_date": day, "press": press, "shift": shift,
                    "shipment_id": f"SH-{ship_day:%m%d}-{press}", "customer": "OEM-J", "status": status,
                })

            # one inspection-plan record per press per day
            rev = "C" if (planted_story and press == "P4" and day >= P4_SAMPLING_CUT) else "B"
            gauge = f"G-0{PRESSES.index(press) + 1}"
            cal_due = G02_CAL_DUE if (planted_story and press == "P2") else date(2026, 12, 15)
            insp_rows.append({
                "insp_date": day, "press": press,
                "sampling_per_hour": 1 if rev == "C" else 5,
                "control_plan_rev": rev, "gauge_id": gauge, "gauge_cal_due": cal_due,
                "inspector": f"INS-{(PRESSES.index(press) * 3 + day.toordinal()) % 9 + 1:02d}",
            })

    production = pd.DataFrame(prod_rows)
    production["defects_total"] = production[[f"defects_{d}" for d in DEFECT_TYPES]].sum(axis=1)
    return PlantData(
        production=production,
        process=pd.DataFrame(proc_rows),
        inspection=pd.DataFrame(insp_rows),
        shipments=pd.DataFrame(ship_rows),
        past_8d=past_8d_table(),
        meta={"seed": seed, "planted_story": planted_story, "extra": [p.__dict__ for p in extra],
              "today": TODAY.isoformat(), "synthetic": True},
    )


def past_8d_table() -> pd.DataFrame:
    rows = [
        ("8D-2026-014", "2026-03-11", "CL-204", "crack", "Press 2 dryer desiccant saturated; resin moisture 0.08%",
         "Moisture not in incoming-resin check", "Dryer dew-point interlock on press 2; moisture test per resin lot",
         "Dryer faults show up as cracks within 1-2 shifts; check dew point first."),
        ("8D-2026-019", "2026-04-02", "CL-204", "burr", "Cavity 1 parting-line wear after skipped PM",
         "Burr gauge out of calibration", "PM interlock at 250k shots; gauge calibration alarm",
         "Burrs on one cavity point to tooling wear, not process settings."),
        ("8D-2026-023", "2026-05-20", "CL-118", "short_shot", "Hot-runner heater zone 3 failure",
         "Short shots below visual sampling rate", "Heater alarm on HMI; sampling 5/h restored",
         "Single-cavity short shots: check hot-runner zones."),
        ("8D-2026-031", "2026-07-08", "CL-204", "scratch", "Damaged conveyor guide on line 3",
         "No scratch characteristic in final check", "Guide replaced; scratch added to final visual check",
         "Scratches spread across presses point to handling, not moulding."),
        ("8D-2025-087", "2025-11-19", "CL-204", "crack", "Operator error claimed; later traced to resin lot moisture",
         "Sampling reduced during staffing shortage", "Moisture check per lot; sampling floor of 3/h",
         "An early 'operator error' finding was wrong; stratify by press before blaming crews."),
        ("8D-2026-002", "2026-01-15", "CL-310", "crack", "Mould temperature controller drift on press 5",
         "Temperature alarm disabled", "Alarm re-enabled; weekly verification",
         "Crack plus cold mould: check the temperature controller."),
    ]
    return pd.DataFrame(rows, columns=["case_id", "closed_date", "part_no", "defect", "occurrence_cause",
                                       "escape_cause", "actions", "lesson"])


if __name__ == "__main__":  # quick look
    pdata = generate()
    for name, df in pdata.tables().items():
        print(name, df.shape)
