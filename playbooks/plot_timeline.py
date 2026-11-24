#!/usr/bin/env python3
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

import matplotlib.pyplot as plt


def parse_iso_utc(ts: str) -> datetime | None:
    ts = (ts or "").strip()
    if not ts:
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def load_row(csv_path: Path, run_id: int | None) -> dict[str, str]:
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise ValueError("CSV has no data rows")

    if run_id is None:
        return rows[-1]

    for row in rows:
        if str(row.get("run_id", "")).strip() == str(run_id):
            return row

    raise ValueError(f"run_id={run_id} not found in {csv_path}")


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("CSV has no data rows")
    return rows


def rel_seconds(ts: datetime | None, origin: datetime) -> float | None:
    if ts is None:
        return None
    return (ts - origin).total_seconds()


def plot_timeline(row: dict[str, str], output_path: Path) -> None:
    requested = parse_iso_utc(row.get("requested_at", ""))
    if requested is None:
        raise ValueError("requested_at is missing; cannot plot timeline")

    events = {
        "Request": parse_iso_utc(row.get("requested_at", "")),
        "Boot ping OK": parse_iso_utc(row.get("boot_ping_ok_at", "")),
        "Node joined": parse_iso_utc(row.get("joined_at", "")),
        "Descheduler launch": parse_iso_utc(row.get("descheduler_launched_at", "")),
        "Descheduler done": parse_iso_utc(row.get("descheduler_done_at", "")),
        "Source stop start": parse_iso_utc(row.get("source_pod_stop_started_at", "")),
        "Source stop done": parse_iso_utc(row.get("source_pod_stop_finished_at", "")),
        "Target setup start": parse_iso_utc(row.get("target_pod_setup_started_at", "")),
        "Target ready": parse_iso_utc(row.get("target_pod_ready_at", "")),
    }

    points: dict[str, float] = {}
    for label, ts in events.items():
        sec = rel_seconds(ts, requested)
        if sec is not None:
            points[label] = sec

    fig, ax = plt.subplots(figsize=(13, 4.8))

    y_events = 1.0
    sorted_points = sorted(points.items(), key=lambda it: it[1])
    for idx, (label, x) in enumerate(sorted_points):
        ax.scatter(x, y_events, s=60)
        label_on_top = idx % 2 == 0
        y_label = y_events + 0.09 if label_on_top else y_events - 0.09
        va = "bottom" if label_on_top else "top"
        ax.plot([x, x], [y_events, y_label], color="0.65", linewidth=0.8)
        ax.text(
            x,
            y_label,
            f"{label}\n{x:.3f}s",
            ha="center",
            va=va,
            fontsize=9,
        )

    # Duration bars
    dur_specs = [
        (
            "Descheduler runtime",
            rel_seconds(events["Descheduler launch"], requested),
            rel_seconds(events["Descheduler done"], requested),
            "tab:orange",
            0.58,
        ),
        (
            "Source pod terminating",
            rel_seconds(events["Source stop start"], requested),
            rel_seconds(events["Source stop done"], requested),
            "tab:red",
            0.36,
        ),
        (
            "Target pod setup->ready",
            rel_seconds(events["Target setup start"], requested),
            rel_seconds(events["Target ready"], requested),
            "tab:green",
            0.14,
        ),
    ]

    for label, start, end, color, y in dur_specs:
        if start is None or end is None:
            continue
        width = max(0.0, end - start)
        ax.barh(y, width, left=start, height=0.12, color=color, alpha=0.85)
        ax.text(
            start + width / 2,
            y,
            f"{label}\n{width:.3f}s",
            ha="center",
            va="center",
            fontsize=9,
            color="white",
        )

    run_id = row.get("run_id", "")
    source_pod = row.get("source_pod_name", "")
    target_pod = row.get("target_pod_name", "")
    title = f"Scale-up Timeline (run_id={run_id}) | {source_pod} -> {target_pod}"

    max_x = max(points.values()) if points else 1.0
    ax.set_xlim(-1.0, max_x + 5.0)
    ax.set_ylim(0.0, 1.32)
    ax.set_yticks([])
    ax.set_xlabel("Seconds since request")
    ax.set_title(title)
    ax.grid(axis="x", linestyle="--", alpha=0.35)

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=160)
    plt.close(fig)


def parse_float(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean": mean(values),
        "std_dev": stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "median": median(values),
    }


def row_event_time(row: dict[str, str], event: str) -> float | None:
    req_to_launch = parse_float(row.get("request_to_descheduler_launch_s", ""))

    if event == "Request":
        return 0.0
    if event == "Boot ping OK":
        return parse_float(row.get("request_to_boot_ping_s", ""))
    if event == "Node joined":
        return parse_float(row.get("request_to_join_s", ""))
    if event == "Descheduler launch":
        return req_to_launch
    if event == "Descheduler done":
        return parse_float(row.get("request_to_descheduler_done_s", ""))

    if req_to_launch is None:
        return None

    if event == "Source stop start":
        offset = parse_float(row.get("descheduler_launch_to_source_stop_start_s", ""))
        return req_to_launch + offset if offset is not None else None
    if event == "Target setup start":
        offset = parse_float(row.get("descheduler_launch_to_target_setup_start_s", ""))
        return req_to_launch + offset if offset is not None else None
    if event == "Target ready":
        offset = parse_float(row.get("descheduler_launch_to_target_pod_ready_s", ""))
        return req_to_launch + offset if offset is not None else None
    if event == "Source stop done":
        offset = parse_float(row.get("descheduler_launch_to_source_stop_done_s", ""))
        return req_to_launch + offset if offset is not None else None

    return None


def build_stats(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    stats_rows: list[dict[str, str]] = []

    event_order = [
        "Request",
        "Boot ping OK",
        "Node joined",
        "Descheduler launch",
        "Descheduler done",
        "Source stop start",
        "Target setup start",
        "Target ready",
        "Source stop done",
    ]

    for label in event_order:
        values = [row_event_time(r, label) for r in rows]
        values = [v for v in values if v is not None]
        if not values:
            continue
        s = summarize(values)
        stats_rows.append(
            {
                "category": "event_time",
                "name": label,
                "count": str(int(s["count"])),
                "mean_s": f"{s['mean']:.6f}",
                "std_dev_s": f"{s['std_dev']:.6f}",
                "min_s": f"{s['min']:.6f}",
                "max_s": f"{s['max']:.6f}",
                "median_s": f"{s['median']:.6f}",
            }
        )

    duration_specs = [
        ("Descheduler runtime", "descheduler_launch_to_done_s"),
        ("Source pod terminating", "source_stop_start_to_stop_done_s"),
        ("Target pod setup->ready", "target_setup_start_to_ready_s"),
    ]
    for label, duration_key in duration_specs:
        dvals = [parse_float(r.get(duration_key, "")) for r in rows]
        dvals = [v for v in dvals if v is not None]
        if not dvals:
            continue
        s = summarize(dvals)
        stats_rows.append(
            {
                "category": "duration",
                "name": label,
                "count": str(int(s["count"])),
                "mean_s": f"{s['mean']:.6f}",
                "std_dev_s": f"{s['std_dev']:.6f}",
                "min_s": f"{s['min']:.6f}",
                "max_s": f"{s['max']:.6f}",
                "median_s": f"{s['median']:.6f}",
            }
        )

    metric_specs = [
        ("request_to_boot_ping_s", "Request -> Boot ping"),
        ("request_to_join_s", "Request -> Join"),
        ("request_to_descheduler_launch_s", "Request -> Descheduler launch"),
        ("request_to_descheduler_done_s", "Request -> Descheduler done"),
        ("descheduler_launch_to_done_s", "Descheduler runtime"),
        ("descheduler_launch_to_source_stop_start_s", "Desch -> Source stop start"),
        ("descheduler_launch_to_source_stop_done_s", "Desch -> Source stop done"),
        ("descheduler_launch_to_target_setup_start_s", "Desch -> Target setup start"),
        ("descheduler_launch_to_target_pod_ready_s", "Desch -> Target ready"),
        ("source_stop_start_to_stop_done_s", "Source stop duration"),
        ("target_setup_start_to_ready_s", "Target setup duration"),
    ]
    for key, label in metric_specs:
        vals = [parse_float(r.get(key, "")) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        s = summarize(vals)
        stats_rows.append(
            {
                "category": "raw_metric",
                "name": label,
                "count": str(int(s["count"])),
                "mean_s": f"{s['mean']:.6f}",
                "std_dev_s": f"{s['std_dev']:.6f}",
                "min_s": f"{s['min']:.6f}",
                "max_s": f"{s['max']:.6f}",
                "median_s": f"{s['median']:.6f}",
            }
        )

    return stats_rows


def write_stats_csv(stats_rows: list[dict[str, str]], output_csv: Path) -> None:
    fieldnames = [
        "category",
        "name",
        "count",
        "mean_s",
        "std_dev_s",
        "min_s",
        "max_s",
        "median_s",
    ]
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in stats_rows:
            writer.writerow(row)


def plot_mean_std(rows: list[dict[str, str]], output_path: Path) -> None:
    event_order = [
        "Request",
        "Boot ping OK",
        "Node joined",
        "Descheduler launch",
        "Descheduler done",
        "Source stop start",
        "Target setup start",
        "Target ready",
        "Source stop done",
    ]

    timeline_points: list[tuple[str, float, float]] = []
    for label in event_order:
        values = [row_event_time(r, label) for r in rows]
        values = [v for v in values if v is not None]
        if not values:
            continue
        m = mean(values)
        s = stdev(values) if len(values) > 1 else 0.0
        timeline_points.append((label, m, s))

    if not timeline_points:
        raise ValueError("No numeric timing columns found to aggregate")

    fig, ax = plt.subplots(figsize=(13, 5.0))

    y_events = 1.0
    for idx, (label, m, s) in enumerate(timeline_points):
        ax.errorbar(
            m,
            y_events,
            xerr=s,
            fmt="o",
            color="tab:blue",
            ecolor="tab:blue",
            capsize=5,
            linewidth=1.6,
        )

        label_on_top = idx % 2 == 0
        y_label = y_events + 0.10 if label_on_top else y_events - 0.10
        va = "bottom" if label_on_top else "top"
        ax.plot([m, m], [y_events, y_label], color="0.65", linewidth=0.8)
        ax.text(
            m,
            y_label,
            f"{label}\n{m:.2f}s±{s:.2f}s",
            ha="center",
            va=va,
            fontsize=8.5,
        )

    # Mean duration bars (same visual style as single-run timeline bars).
    mean_points = {label: m for label, m, _ in timeline_points}
    duration_specs = [
        (
            "Descheduler runtime",
            mean_points.get("Descheduler launch"),
            "descheduler_launch_to_done_s",
            "tab:orange",
            0.58,
        ),
        (
            "Source pod terminating",
            mean_points.get("Source stop start"),
            "source_stop_start_to_stop_done_s",
            "tab:red",
            0.36,
        ),
        (
            "Target pod setup->ready",
            mean_points.get("Target setup start"),
            "target_setup_start_to_ready_s",
            "tab:green",
            0.14,
        ),
    ]

    for label, start_mean, duration_key, color, y in duration_specs:
        if start_mean is None:
            continue
        dvals = [parse_float(r.get(duration_key, "")) for r in rows]
        dvals = [v for v in dvals if v is not None]
        if not dvals:
            continue
        dmean = mean(dvals)
        dstd = stdev(dvals) if len(dvals) > 1 else 0.0
        ax.barh(y, dmean, left=start_mean, height=0.12, color=color, alpha=0.85)
        ax.text(
            start_mean + dmean / 2,
            y,
            f"{label}\n{dmean:.2f}s±{dstd:.2f}s",
            ha="center",
            va="center",
            fontsize=8.5,
            color="white",
        )

    max_x = max(m + s for _, m, s in timeline_points)
    ax.set_xlim(-1.0, max_x + 6.0)
    ax.set_ylim(0.0, 1.34)
    ax.set_yticks([])
    ax.set_xlabel("Seconds since request")
    ax.set_title(f"Scale-up Timeline Mean ± Std Dev (n={len(rows)})")
    ax.grid(axis="x", linestyle="--", alpha=0.35)

    fig.tight_layout()
    fig.savefig(str(output_path), dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot scale-up run timeline from CSV")
    parser.add_argument(
        "--csv",
        default="cluster_state_log_3.csv",
        help="Input CSV file path",
    )
    parser.add_argument(
        "--run-id",
        type=int,
        default=None,
        help="Run ID to plot (default: last row)",
    )
    parser.add_argument(
        "--output",
        default="scale_up_timeline.png",
        help="Output image path",
    )
    parser.add_argument(
        "--mode",
        choices=["timeline", "mean-std"],
        default="timeline",
        help="Plot mode: single-run timeline or aggregate mean/std across runs",
    )
    parser.add_argument(
        "--stats-output",
        default="",
        help="Optional output CSV path for aggregate stats (use with --mode mean-std)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    output_path = Path(args.output)

    if args.mode == "timeline":
        row = load_row(csv_path, args.run_id)
        plot_timeline(row, output_path)
    else:
        rows = load_rows(csv_path)
        plot_mean_std(rows, output_path)
        if args.stats_output:
            stats_rows = build_stats(rows)
            write_stats_csv(stats_rows, Path(args.stats_output))
            print(f"Saved stats CSV to {args.stats_output}")

    print(f"Saved timeline plot to {output_path}")


if __name__ == "__main__":
    main()
