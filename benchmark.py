import csv
import os
import time
import statistics
from collections import defaultdict

import requests

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000/chat")
RUNS_PER_QUESTION = int(os.getenv("RUNS_PER_QUESTION", "8"))
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "60"))

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
    "Compară Eiffel Tower cu Seine River Cruise: prețuri + ce trebuie să știu înainte să merg."
]


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = int((p / 100) * (len(values) - 1))
    return values[k]


def main():
    out_rows = []
    failures = 0
    total = len(QUESTIONS) * RUNS_PER_QUESTION
    done = 0
    print(f"Benchmark start: {len(QUESTIONS)} questions x {RUNS_PER_QUESTION} runs = {total} requests")

    for q in QUESTIONS:
        for i in range(RUNS_PER_QUESTION):
            t0 = time.perf_counter()
            try:
                done += 1
                print(f"[{done}/{total}] run={i+1}/{RUNS_PER_QUESTION} | q='{q[:60]}...'")
                r = requests.post(API_URL, json={"question": q}, timeout=TIMEOUT_SEC)
                dt = (time.perf_counter() - t0) * 1000

                status = r.status_code
                if status != 200:
                    failures += 1
                    out_rows.append({
                        "question": q,
                        "run": i + 1,
                        "http_status": status,
                        "client_latency_ms": round(dt, 2),
                        "execution_flow": "",
                        "server_latency_ms": "",
                    })
                    continue

                data = r.json()
                print(f"    -> {data.get('execution_flow')} | server={data.get('latency_ms')}ms | http={status}")
                out_rows.append({
                    "question": q,
                    "run": i + 1,
                    "http_status": status,
                    "client_latency_ms": round(dt, 2),
                    "execution_flow": data.get("execution_flow", ""),
                    "server_latency_ms": data.get("latency_ms", ""),
                })
            except Exception:
                failures += 1
                dt = (time.perf_counter() - t0) * 1000
                out_rows.append({
                    "question": q,
                    "run": i + 1,
                    "http_status": "EXCEPTION",
                    "client_latency_ms": round(dt, 2),
                    "execution_flow": "",
                    "server_latency_ms": "",
                })

    # Write CSV
    csv_path = "performance_results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "question", "run", "http_status", "execution_flow", "server_latency_ms", "client_latency_ms"
        ])
        writer.writeheader()
        writer.writerows(out_rows)

    # Summary by flow (using server latency if present)
    by_flow = defaultdict(list)
    for row in out_rows:
        flow = row["execution_flow"] or "UNKNOWN"
        try:
            ms = float(row["server_latency_ms"])
            by_flow[flow].append(ms)
        except Exception:
            pass

    print(f"Wrote {csv_path}")
    print(f"Failures: {failures}/{len(out_rows)}")

    for flow, vals in by_flow.items():
        print(f"\nFlow: {flow}")
        print(f"  n={len(vals)}")
        print(f"  mean={statistics.mean(vals):.2f} ms")
        print(f"  p50 ={pct(vals, 50):.2f} ms")
        print(f"  p95 ={pct(vals, 95):.2f} ms")
        print(f"  max ={max(vals):.2f} ms")


if __name__ == "__main__":
    main()