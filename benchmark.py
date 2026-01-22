import csv
import json
import os
import time
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

API_URL = os.getenv(
    "API_URL",
    "https://webapp-rag-dtchdma6f5cnesb6.francecentral-01.azurewebsites.net/chat",
)
RUNS_PER_QUESTION = int(os.getenv("RUNS_PER_QUESTION", "2"))
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "60"))
PLOT = os.getenv("PLOT", "1") == "1"

QUESTIONS = [
    # SQL-only-ish (program / price)
    "Care este programul pentru Louvre Museum?",
    "Care este prețul biletului Adult la Louvre Museum?",
    "Este Musée d'Orsay deschis luni?",
    "Care este cel mai ieftin bilet disponibil și pentru ce atracție?",

    # Search-only (rules / safety / tips)
    "Ce reguli de securitate ar trebui să respect la atracțiile din Paris?",
    "Ce sfaturi ai pentru a evita aglomerația la principalele atracții?",
    "Ce recomandări ai despre transportul public în Paris (validare, reguli)?",
    "Ce pot vizita în Paris într-o zi ploioasă și de ce?",

    # Mix (SQL + Search + LLM)
    "Vreau să vizitez Louvre Museum: spune-mi programul și ce reguli de acces ar trebui să știu.",
    "Recomandă-mi o atracție sub 20 EUR și explică regulile/condițiile de acces.",
    "Spune-mi dacă Musée d'Orsay e deschis marți și ce sfaturi ai pentru vizită.",
    "Compară Eiffel Tower cu Seine River Cruise: prețuri + ce trebuie să știu înainte să merg.",
]


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = int((p / 100) * (len(values) - 1))
    return values[k]


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def compute_flow_stats(rows):
    by_flow = defaultdict(list)
    for row in rows:
        flow = row.get("execution_flow") or "UNKNOWN"
        ms = safe_float(row.get("server_latency_ms"))
        if ms is not None:
            by_flow[flow].append(ms)

    stats = {}
    for flow, vals in by_flow.items():
        if not vals:
            continue
        stats[flow] = {
            "n": len(vals),
            "mean_ms": statistics.mean(vals),
            "p50_ms": pct(vals, 50),
            "p95_ms": pct(vals, 95),
            "max_ms": max(vals),
        }
    return stats


def write_flow_summary(stats: dict, out_csv: Path, out_json: Path):
    # CSV
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["flow", "n", "mean_ms", "p50_ms", "p95_ms", "max_ms"])
        for flow, s in sorted(stats.items()):
            w.writerow([flow, s["n"], round(s["mean_ms"], 2), round(s["p50_ms"], 2), round(s["p95_ms"], 2), round(s["max_ms"], 2)])

    # JSON
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "stats_by_flow": stats,
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def plot_stats(stats: dict, rows, out_dir: Path):
    # optional plotting
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[plot] matplotlib not available ({e}); skipping plots.")
        return

    flows = sorted(stats.keys())
    if not flows:
        print("[plot] No flows to plot.")
        return

    p50s = [stats[f]["p50_ms"] for f in flows]
    p95s = [stats[f]["p95_ms"] for f in flows]

    # Bar chart: p50 vs p95
    fig = plt.figure(figsize=(10, 5))
    x = range(len(flows))
    width = 0.35
    plt.bar([i - width/2 for i in x], p50s, width=width, label="p50")
    plt.bar([i + width/2 for i in x], p95s, width=width, label="p95")
    plt.xticks(list(x), flows, rotation=20, ha="right")
    plt.ylabel("Latency (ms) [server_latency_ms]")
    plt.title("Latency by flow (p50 vs p95)")
    plt.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "latency_by_flow_p50_p95.png", dpi=160)
    plt.close(fig)

    # Boxplot distribution by flow
    by_flow_vals = defaultdict(list)
    for row in rows:
        flow = row.get("execution_flow") or "UNKNOWN"
        ms = safe_float(row.get("server_latency_ms"))
        if ms is not None:
            by_flow_vals[flow].append(ms)

    data = [by_flow_vals[f] for f in flows]
    fig = plt.figure(figsize=(10, 5))
    plt.boxplot(data, labels=flows, showfliers=True)
    plt.xticks(rotation=20, ha="right")
    plt.ylabel("Latency (ms) [server_latency_ms]")
    plt.title("Latency distribution by flow (boxplot)")
    plt.tight_layout()
    fig.savefig(out_dir / "latency_boxplot.png", dpi=160)
    plt.close(fig)


def main():
    out_rows = []
    failures = 0
    total = len(QUESTIONS) * RUNS_PER_QUESTION
    done = 0

    print(f"Benchmark start: {len(QUESTIONS)} questions x {RUNS_PER_QUESTION} runs = {total} requests")
    print(f"API_URL={API_URL}")

    for q in QUESTIONS:
        for i in range(RUNS_PER_QUESTION):
            t0 = time.perf_counter()
            try:
                done += 1
                print(f"[{done}/{total}] run={i+1}/{RUNS_PER_QUESTION} | q='{q[:60]}...'")

                r = requests.post(API_URL, json={"question": q}, timeout=TIMEOUT_SEC)
                client_ms = (time.perf_counter() - t0) * 1000

                status = r.status_code
                if status != 200:
                    failures += 1
                    out_rows.append({
                        "question": q,
                        "run": i + 1,
                        "http_status": status,
                        "client_latency_ms": round(client_ms, 2),
                        "execution_flow": "",
                        "server_latency_ms": "",
                    })
                    print(f"    -> http={status} client={client_ms:.2f}ms (no JSON)")
                    continue

                data = r.json()
                flow = data.get("execution_flow", "")
                server_ms = data.get("latency_ms", "")
                print(f"    -> {flow} | server={server_ms}ms | http={status} | client={client_ms:.2f}ms")

                out_rows.append({
                    "question": q,
                    "run": i + 1,
                    "http_status": status,
                    "client_latency_ms": round(client_ms, 2),
                    "execution_flow": flow,
                    "server_latency_ms": server_ms,
                })
            except Exception as e:
                failures += 1
                client_ms = (time.perf_counter() - t0) * 1000
                out_rows.append({
                    "question": q,
                    "run": i + 1,
                    "http_status": "EXCEPTION",
                    "client_latency_ms": round(client_ms, 2),
                    "execution_flow": "",
                    "server_latency_ms": "",
                })
                print(f"    -> EXCEPTION ({e}) client={client_ms:.2f}ms")

    # Write raw CSV
    csv_path = Path("performance_results.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["question", "run", "http_status", "execution_flow", "server_latency_ms", "client_latency_ms"],
        )
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\nWrote {csv_path}")
    print(f"Failures: {failures}/{len(out_rows)}")

    # Summary
    stats = compute_flow_stats(out_rows)
    for flow, s in sorted(stats.items()):
        print(f"\nFlow: {flow}")
        print(f"  n={s['n']}")
        print(f"  mean={s['mean_ms']:.2f} ms")
        print(f"  p50 ={s['p50_ms']:.2f} ms")
        print(f"  p95 ={s['p95_ms']:.2f} ms")
        print(f"  max ={s['max_ms']:.2f} ms")

    # Write summary artifacts
    out_dir = Path("plots")
    ensure_dir(out_dir)
    write_flow_summary(stats, out_dir / "flow_summary.csv", out_dir / "flow_summary.json")
    print(f"\nWrote {out_dir/'flow_summary.csv'} and {out_dir/'flow_summary.json'}")

    if PLOT:
        plot_stats(stats, out_rows, out_dir)
        print(f"Wrote plots in {out_dir}/ (png files)")


if __name__ == "__main__":
    main()