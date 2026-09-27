from __future__ import annotations

import csv
from pathlib import Path

IN_CSV = Path("docs/strength_models_ab_comparison_2026-09-24.csv")
OUT_CSV = Path("docs/strength_model_b_recalibrated_2026-09-27.csv")
OUT_MD = Path("docs/strength_model_b_recalibrated_2026-09-27.md")

HR_REST = 40.714285714285715
LTHR = 169.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def corr(a: list[float], b: list[float]) -> float:
    ma = mean(a)
    mb = mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / ((va**0.5) * (vb**0.5))


def main() -> None:
    rows: list[dict[str, str]] = []
    with IN_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("calc_error") or "").strip():
                continue
            rows.append(row)

    points: list[tuple[dict[str, str], float, float]] = []
    for row in rows:
        tp = float(row["tp_local"])
        hours = float(row["duration_h"])
        avg_hr = float(row["average_hr"])

        if_target = (tp / (100.0 * hours)) ** 0.5
        z = (avg_hr - HR_REST) / (LTHR - HR_REST)
        z_c = max(0.0, min(1.15, z))
        points.append((row, z_c, if_target))

    zs = [x[1] for x in points]
    ys = [x[2] for x in points]
    z_bar = mean(zs)
    y_bar = mean(ys)
    var_z = sum((z - z_bar) ** 2 for z in zs)
    if var_z <= 0:
        raise RuntimeError("Cannot fit LS line: zero variance in z_c")

    slope = sum((z - z_bar) * (y - y_bar) for z, y in zip(zs, ys)) / var_z
    intercept = y_bar - slope * z_bar

    recal_rows: list[dict[str, str]] = []
    for row, z_c, if_target in points:
        tp = float(row["tp_local"])
        hours = float(row["duration_h"])

        if_raw = intercept + slope * z_c
        if_eff = max(0.45, min(0.80, if_raw))
        tss_recal = 100.0 * hours * (if_eff**2)

        tss_b = float(row["tss_model_b"])
        d_b = tss_b - tp
        d_recal = tss_recal - tp

        recal_rows.append(
            {
                "fecha": row["fecha"],
                "activity_id": row["activity_id"],
                "segment": row.get("segment", ""),
                "tp_local": f"{tp:.6f}",
                "duration_h": f"{hours:.9f}",
                "average_hr": row["average_hr"],
                "z_c": f"{z_c:.9f}",
                "if_target": f"{if_target:.9f}",
                "if_b_current": "",
                "if_b_raw_recal": f"{if_raw:.9f}",
                "if_b_eff_recal": f"{if_eff:.9f}",
                "tss_b_current": f"{tss_b:.6f}",
                "tss_b_recal": f"{tss_recal:.6f}",
                "delta_b_current": f"{d_b:.6f}",
                "delta_b_recal": f"{d_recal:.6f}",
                "ratio_b_current": f"{(tss_b / tp):.9f}",
                "ratio_b_recal": f"{(tss_recal / tp):.9f}",
            }
        )

    recal_rows.sort(key=lambda r: r["fecha"], reverse=True)

    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recal_rows[0].keys()))
        w.writeheader()
        w.writerows(recal_rows)

    b_deltas = [float(r["delta_b_current"]) for r in recal_rows]
    b_ratios = [float(r["ratio_b_current"]) for r in recal_rows]
    r_deltas = [float(r["delta_b_recal"]) for r in recal_rows]
    r_ratios = [float(r["ratio_b_recal"]) for r in recal_rows]

    b_mae = mean([abs(x) for x in b_deltas])
    b_bias = mean(b_deltas)
    b_ratio = mean(b_ratios)

    r_mae = mean([abs(x) for x in r_deltas])
    r_bias = mean(r_deltas)
    r_ratio = mean(r_ratios)

    if_raw_vals = [float(r["if_b_raw_recal"]) for r in recal_rows]
    if_eff_vals = [float(r["if_b_eff_recal"]) for r in recal_rows]
    hit_low = sum(1 for v in if_raw_vals if v < 0.45)
    hit_high = sum(1 for v in if_raw_vals if v > 0.80)

    duration_vals = [float(r["duration_h"]) for r in recal_rows]
    tp_vals = [float(r["tp_local"]) for r in recal_rows]
    z_vals = [float(r["z_c"]) for r in recal_rows]
    corr_duration_tp = corr(duration_vals, tp_vals)
    corr_z_tp = corr(z_vals, tp_vals)
    corr_duration_z = corr(duration_vals, z_vals)

    lines: list[str] = []
    lines.append("# Strength B Recalibration (Least Squares)")
    lines.append("")
    lines.append("Date: 2026-09-27")
    lines.append("Status: in-sample calibration only, pending out-of-sample confirmation.")
    lines.append("")
    lines.append("## Inputs")
    lines.append(f"- sessions_n: {len(recal_rows)}")
    lines.append(f"- hr_rest_used: {HR_REST:.6f}")
    lines.append(f"- lthr_bpm: {LTHR:.3f}")
    lines.append("")
    lines.append("## Fitted Coefficients")
    lines.append(f"- intercept: {intercept:.9f}")
    lines.append(f"- slope: {slope:.9f}")
    lines.append("")
    lines.append("## Clamp Check")
    lines.append(f"- if_raw_min: {min(if_raw_vals):.9f}")
    lines.append(f"- if_raw_max: {max(if_raw_vals):.9f}")
    lines.append(f"- if_eff_min: {min(if_eff_vals):.9f}")
    lines.append(f"- if_eff_max: {max(if_eff_vals):.9f}")
    lines.append(f"- clamp_hits_low(<0.45): {hit_low}")
    lines.append(f"- clamp_hits_high(>0.80): {hit_high}")
    lines.append("")
    lines.append("## Interpretation Guardrails")
    lines.append("- WARNING: calibration was fit on a narrow z_c range only; outside that range the model extrapolates without validation.")
    lines.append(
        f"- z_c_support_range: [{min(z_vals):.6f}, {max(z_vals):.6f}] (span={max(z_vals)-min(z_vals):.6f} over theoretical [0, 1.15])."
    )
    lines.append(
        f"- corr(duration_h, tp_local): {corr_duration_tp:.6f} (very high values indicate TP is largely duration-driven in this sample)."
    )
    lines.append(f"- corr(z_c, tp_local): {corr_z_tp:.6f}")
    lines.append(f"- corr(duration_h, z_c): {corr_duration_z:.6f}")
    lines.append("- The in-sample MAE should not be interpreted as general precision across medium/high-intensity strength sessions.")
    lines.append("")
    lines.append("## Metrics (same 18 sessions)")
    lines.append("| Variant | n | MAE | Bias | Ratio mean |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(f"| B current | {len(recal_rows)} | {b_mae:.6f} | {b_bias:.6f} | {b_ratio:.6f} |")
    lines.append(f"| B recalibrated (LS) | {len(recal_rows)} | {r_mae:.6f} | {r_bias:.6f} | {r_ratio:.6f} |")
    lines.append("")
    lines.append("## Segment Diagnostics (same 18 sessions)")
    lines.append("Interpret with caution: each segment also lives in a narrow sub-range of z_c.")
    lines.append("| Segment | n | z_c min | z_c max | MAE B current | MAE B recal | Bias B current | Bias B recal | Ratio B current | Ratio B recal |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    by_segment: dict[str, list[dict[str, str]]] = {}
    for r in recal_rows:
        by_segment.setdefault(r["segment"], []).append(r)

    for seg, seg_rows in sorted(by_segment.items()):
        z_seg = [float(r["z_c"]) for r in seg_rows]
        d0 = [float(r["delta_b_current"]) for r in seg_rows]
        d1 = [float(r["delta_b_recal"]) for r in seg_rows]
        rr0 = [float(r["ratio_b_current"]) for r in seg_rows]
        rr1 = [float(r["ratio_b_recal"]) for r in seg_rows]
        lines.append(
            f"| {seg} | {len(seg_rows)} | {min(z_seg):.4f} | {max(z_seg):.4f} | {mean([abs(x) for x in d0]):.6f} | {mean([abs(x) for x in d1]):.6f} | {mean(d0):.6f} | {mean(d1):.6f} | {mean(rr0):.6f} | {mean(rr1):.6f} |"
        )
    lines.append("")
    lines.append("## Recalibrated Per-session")
    lines.append("| Date | Activity ID | Segment | TP | z_c | IF_target | IF_recal_raw | IF_recal_eff | TSS_B_current | TSS_B_recal | ratio_B_current | ratio_B_recal |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in recal_rows:
        lines.append(
            "| {fecha} | {activity_id} | {segment} | {tp} | {zc} | {ift} | {ifr} | {ife} | {tb} | {tr} | {rb} | {rr} |".format(
                fecha=r["fecha"],
                activity_id=r["activity_id"],
                segment=r["segment"],
                tp=f"{float(r['tp_local']):.3f}",
                zc=f"{float(r['z_c']):.4f}",
                ift=f"{float(r['if_target']):.4f}",
                ifr=f"{float(r['if_b_raw_recal']):.4f}",
                ife=f"{float(r['if_b_eff_recal']):.4f}",
                tb=f"{float(r['tss_b_current']):.3f}",
                tr=f"{float(r['tss_b_recal']):.3f}",
                rb=f"{float(r['ratio_b_current']):.3f}",
                rr=f"{float(r['ratio_b_recal']):.3f}",
            )
        )

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"n={len(recal_rows)}")
    print(f"intercept={intercept:.9f}")
    print(f"slope={slope:.9f}")
    print(f"b_current_mae={b_mae:.6f}")
    print(f"b_current_bias={b_bias:.6f}")
    print(f"b_current_ratio_mean={b_ratio:.6f}")
    print(f"b_recal_mae={r_mae:.6f}")
    print(f"b_recal_bias={r_bias:.6f}")
    print(f"b_recal_ratio_mean={r_ratio:.6f}")
    print(f"if_raw_min={min(if_raw_vals):.6f}")
    print(f"if_raw_max={max(if_raw_vals):.6f}")
    print(f"clamp_hits_low={hit_low}")
    print(f"clamp_hits_high={hit_high}")
    print(f"out_csv={OUT_CSV.as_posix()}")
    print(f"out_md={OUT_MD.as_posix()}")


if __name__ == "__main__":
    main()
