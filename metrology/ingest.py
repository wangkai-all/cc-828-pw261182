"""CSV ingestion, synthetic designs and traceable fixtures.

The synthetic generator writes a plain CSV whose header records the true
variance components used to simulate.  Nothing is hidden: the same file can be
re-uploaded, inspected in a text editor, and re-fitted.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

REQUIRED = ("lab", "device", "date", "value")
ALIASES = {
    "lab": {"lab", "laboratory", "实验室", "实验室编号", "lab_id", "lab id"},
    "device": {"device", "仪器", "设备", "设备编号", "instrument", "device_id", "device id"},
    "date": {"date", "日期", "measurement_date", "run_date", "run date"},
    "value": {"value", "measurement", "result", "测量值", "结果", "y"},
}


class IngestError(ValueError):
    pass


def _canonical(header: str) -> Optional[str]:
    h = header.strip().lower()
    for key, names in ALIASES.items():
        if h in {n.lower() for n in names}:
            return key
    return None


def parse_csv(text: str) -> List[Dict[str, object]]:
    """Parse a CSV with lab/device/date/value columns into typed rows."""
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise IngestError("CSV 为空。")
    mapping = {}
    for j, h in enumerate(header):
        key = _canonical(h)
        if key:
            mapping[key] = j
    missing = [k for k in REQUIRED if k not in mapping]
    if missing:
        names = {"lab": "实验室", "device": "设备", "date": "日期", "value": "测量值"}
        raise IngestError(
            "缺少列：" + "、".join(names[m] for m in missing)
            + "。表头需要 lab, device, date, value（也接受中文列名）。"
        )
    rows: List[Dict[str, object]] = []
    for lineno, rec in enumerate(reader, start=2):
        if not any(cell.strip() for cell in rec):
            continue
        if len(rec) <= max(mapping.values()):
            raise IngestError(f"第 {lineno} 行列数不足。")
        rows.append(
            {
                "lab": rec[mapping["lab"]].strip(),
                "device": rec[mapping["device"]].strip(),
                "date": rec[mapping["date"]].strip(),
                "value": rec[mapping["value"]].strip(),
            }
        )
    if not rows:
        raise IngestError("CSV 只有表头，没有数据行。")
    return rows


def rows_to_csv(rows: Sequence[Dict[str, object]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["lab", "device", "date", "value"])
    for r in rows:
        w.writerow([r["lab"], r["device"], r["date"], r["value"]])
    return buf.getvalue()


def fingerprint(rows: Sequence[Dict[str, object]]) -> str:
    payload = json.dumps(
        [[r["lab"], r["device"], r["date"], round(float(r["value"]), 10)] for r in rows],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Synthetic generator
# ---------------------------------------------------------------------------

PRESETS = {
    "nested_clean": {
        "label": "嵌套 · 设备仅在一个日期测量",
        "description": "4 家实验室 × 2 台设备，每台设备在一个日期测 3 次。实验室、设备、残差均可分离。",
        "terms": ["lab", "device"],
        "labs": 4,
        "devices_per_lab": 2,
        "dates": ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"],
        "device_dates": 1,
        "reps": 3,
        "truth": {"lab": 4.0, "device": 1.0, "residual": 0.36},
        "mu": 50.0,
    },
    "crossed_lab_date": {
        "label": "交叉 · 实验室 × 日期（含交互）",
        "description": "6 家实验室，每家 1 台设备，在全部 4 个日期各测 3 次。可同时估计实验室、日期、实验室×日期与残差。",
        "terms": ["lab", "date", "lab:date"],
        "labs": 6,
        "devices_per_lab": 1,
        "dates": ["2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09"],
        "device_dates": 4,
        "reps": 3,
        "truth": {"lab": 2.25, "date": 0.8, "lab:date": 0.4, "residual": 0.25},
        "mu": 12.0,
    },
    "crossed_disconnected": {
        "label": "交叉 · 断开连接（应报不可分离）",
        "description": "两组实验室分别只在自己的日期测量，连接图断开。用于检查诊断是否报告不可分离。",
        "terms": ["lab", "date", "lab:date"],
        "labs": 4,
        "devices_per_lab": 1,
        "dates": ["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07"],
        "device_dates": 1,
        "reps": 3,
        "truth": {"lab": 1.0, "date": 0.5, "lab:date": 0.2, "residual": 0.3},
        "mu": 3.0,
        "disconnect": True,
    },
    "boundary_zero": {
        "label": "边界 · 一个分量为零",
        "description": "6 家实验室 × 3 个日期，模拟时日期方差为 0。检查非负约束是否正确停在 0，而不是给出负数或需要加常数。",
        "terms": ["lab", "date", "lab:date"],
        "labs": 4,
        "devices_per_lab": 1,
        "dates": ["2026-06-01", "2026-06-02", "2026-06-03"],
        "device_dates": 3,
        "reps": 3,
        "truth": {"lab": 1.6, "date": 0.0, "lab:date": 0.3, "residual": 0.5},
        "mu": 100.0,
    },
}


def _normalized_preset(name: str) -> Dict[str, object]:
    if name not in PRESETS:
        raise IngestError(f"未知预设: {name}")
    return PRESETS[name]


def simulate(preset: Dict[str, object], seed: int) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """Simulate a design from a preset; returns rows and a truth record."""
    rng = np.random.default_rng(seed)
    labs = [f"L{i + 1:02d}" for i in range(int(preset["labs"]))]
    dates = list(preset["dates"])
    devs_per_lab = int(preset["devices_per_lab"])
    reps = int(preset["reps"])
    truth = dict(preset["truth"])
    mu = float(preset["mu"])
    terms = list(preset["terms"])

    lab_devices = {l: [f"LAB{l[1:]}-D{k + 1}" for k in range(devs_per_lab)] for l in labs}

    def sd(key: str) -> float:
        return math.sqrt(max(float(truth.get(key, 0.0)), 0.0))

    u_lab = {l: rng.normal(0, sd("lab")) for l in labs}
    u_date = {d: rng.normal(0, sd("date")) for d in dates}
    u_dev: Dict[Tuple[str, str], float] = {}
    u_ld: Dict[Tuple[str, str], float] = {}
    u_dd: Dict[Tuple[str, str, str], float] = {}

    for l in labs:
        for dev in lab_devices[l]:
            u_dev[(l, dev)] = rng.normal(0, sd("device"))
    for l in labs:
        for dt in dates:
            u_ld[(l, dt)] = rng.normal(0, sd("lab:date"))
    for l in labs:
        for dev in lab_devices[l]:
            for dt in dates:
                u_dd[(l, dev, dt)] = rng.normal(0, sd("device:date"))

    rows: List[Dict[str, object]] = []
    if preset.get("disconnect"):
        # Two blocks: labs 1-2 only on dates 1-2, labs 3-4 only on dates 3-4.
        assignments = {}
        half_d = len(dates) // 2
        half_l = len(labs) // 2
        for i, l in enumerate(labs):
            ds = dates[:half_d] if i < half_l else dates[half_d:]
            for dev in lab_devices[l]:
                assignments[(l, dev)] = ds[(hash((l, dev)) % len(ds))]
        schedule = [(l, dev, assignments[(l, dev)]) for l in labs for dev in lab_devices[l]]
    else:
        schedule = []
        for l in labs:
            for j, dev in enumerate(lab_devices[l]):
                d_count = int(preset.get("device_dates", 1))
                for k in range(d_count):
                    dt = dates[(j + k) % len(dates)]
                    schedule.append((l, dev, dt))
        # Deduplicate (device, date) pairs.
        seen = set()
        uniq = []
        for l, dev, dt in schedule:
            key = (l, dev, dt)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(key)
        schedule = uniq

    for l, dev, dt in schedule:
        base = mu + u_lab[l] + u_dev[(l, dev)] + u_date.get(dt, 0.0)
        if "lab:date" in terms:
            base += u_ld[(l, dt)]
        if "device:date" in terms:
            base += u_dd[(l, dev, dt)]
        for _ in range(reps):
            y = base + rng.normal(0, sd("residual"))
            rows.append({"lab": l, "device": dev, "date": dt, "value": round(float(y), 6)})

    meta = {
        "preset": preset.get("label"),
        "seed": seed,
        "truth": truth,
        "mu": mu,
        "terms": terms,
        "n_rows": len(rows),
        "labs": len(labs),
        "devices": sum(len(v) for v in lab_devices.values()),
        "dates": len(dates),
        "reps": reps,
    }
    return rows, meta


def preset_catalog() -> List[Dict[str, object]]:
    return [
        {
            "id": k,
            "label": v["label"],
            "description": v["description"],
            "terms": v["terms"],
            "truth": v["truth"],
        }
        for k, v in PRESETS.items()
    ]
