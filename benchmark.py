import csv
import json
import os
import time
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import requests

# Configurații din variabile de mediu sau default
API_URL = os.getenv(
    "API_URL",
    "https://webapp-rag-dtchdma6f5cnesb6.francecentral-01.azurewebsites.net/chat",
)
RUNS_PER_QUESTION = int(os.getenv("RUNS_PER_QUESTION", "2"))
TIMEOUT_SEC = int(os.getenv("TIMEOUT_SEC", "60"))
PLOT = os.getenv("PLOT", "1") == "1"

QUESTIONS = [
    # --- CATEGORIA 1: SQL-ONLY (Date structurate din tabelele tale) ---
    # Testează: Keyword routing + Nume parțial + Ziua săptămânii
    "Care este programul la Eiffel Tower sâmbăta?",
    "Cât costă biletul pentru un adult la Louvre Museum?",
    "Este deschis la Sainte-Chapelle duminica?",
    "Care sunt orele de vizitare pentru Arc de Triomphe luni?",
    "Spune-mi prețul biletului la Panthéon.",

    # --- CATEGORIA 2: SEARCH-ONLY (Informații din documentele .txt) ---
    # Testează: Lipsa keyword-urilor SQL -> Rutare automată către Search
    "Ce reguli de securitate sunt în vigoare la monumentele din Paris?",
    "Cum se face validarea biletelor în transportul public?",
    "Ce sfaturi ai pentru a evita cozile la atracțiile principale?",
    "Există restricții pentru vizita exterioară la Notre-Dame?",
    "Ce recomandări ai pentru transportul cu metroul în zona turistică?",

    # --- CATEGORIA 3: MIX - SQL + SEARCH + LLM (Convergența maximă) ---
    # Testează: Detecție dublă (Mix Flow) pentru răspunsuri complexe
    "Vreau la Versailles: cât costă biletul Passport și ce sfaturi de călătorie ai pentru această zonă?",
    "Spune-mi dacă Musée d'Orsay e deschis marți și ce reguli de acces ar trebui să cunosc.",
    "Compară prețul de la Panthéon cu cel de la Sainte-Chapelle și oferă-mi sfaturi despre securitate la ambele.",
    "La ce oră se deschide Centre Pompidou și ce ar trebui să știu despre vizitarea muzeelor în Paris?",
    "Care este prețul biletului la Seine River Cruise și ce trebuie să am în vedere înainte de îmbarcare?",

    # --- CATEGORIA 4: EDGE CASES (Agregări și Articulări) ---
    # Testează: Logica de "cel mai ieftin" și cuvinte articulate
    "Care este cea mai ieftină atracție disponibilă?",
    "Prețurile pentru Louvre sunt mai mari decât la Orsay?",
    "Spune-mi orarul de funcționare pentru Versailles.",
    "Care este biletul cu prețul cel mai mic din baza de date?",
    "Cât costă accesul la Turnul Eiffel pentru copii?"
]

def pct(values, p):
    if not values: return None
    values = sorted(values)
    k = int((p / 100) * (len(values) - 1))
    return values[k]

def safe_float(x):
    try: return float(x)
    except: return None

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
        if not vals: continue
        stats[flow] = {
            "n": len(vals),
            "mean_ms": statistics.mean(vals),
            "p50_ms": pct(vals, 50),
            "p95_ms": pct(vals, 95),
            "max_ms": max(vals),
        }
    return stats

def write_flow_summary(stats: dict, out_csv: Path, out_json: Path):
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["flow", "n", "mean_ms", "p50_ms", "p95_ms", "max_ms"])
        for flow, s in sorted(stats.items()):
            w.writerow([flow, s["n"], round(s["mean_ms"], 2), round(s["p50_ms"], 2), round(s["p95_ms"], 2), round(s["max_ms"], 2)])
    
    payload = {"generated_at": datetime.utcnow().isoformat() + "Z", "stats_by_flow": stats}
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

def plot_stats(stats: dict, rows, out_dir: Path):
    try:
        import matplotlib
        matplotlib.use("Agg") 
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[plot] Matplotlib indisponibil: {e}")
        return

    flows = sorted(stats.keys())
    if not flows: return

    p50s = [stats[f]["p50_ms"] for f in flows]
    p95s = [stats[f]["p95_ms"] for f in flows]

    # Grafic 1: Bar Chart p50 vs p95
    fig = plt.figure(figsize=(12, 6))
    x = range(len(flows))
    width = 0.35
    plt.bar([i - width/2 for i in x], p50s, width=width, label="Mediana (p50)")
    plt.bar([i + width/2 for i in x], p95s, width=width, label="Worst-case (p95)")
    plt.xticks(list(x), flows, rotation=25, ha="right")
    plt.ylabel("Latență Server (ms)")
    plt.title("Performanță per Flow de Execuție (p50 vs p95)")
    plt.legend()
    plt.tight_layout()
    fig.savefig(out_dir / "latency_by_flow_p50_p95.png", dpi=160)
    plt.close(fig)

    # Grafic 2: Boxplot (Distribuție)
    by_flow_vals = defaultdict(list)
    for row in rows:
        flow = row.get("execution_flow") or "UNKNOWN"
        ms = safe_float(row.get("server_latency_ms"))
        if ms is not None: by_flow_vals[flow].append(ms)

    data = [by_flow_vals[f] for f in flows]
    fig = plt.figure(figsize=(12, 6))
    plt.boxplot(data, labels=flows, showfliers=True)
    plt.xticks(rotation=25, ha="right")
    plt.ylabel("Latență Server (ms)")
    plt.title("Distribuția Latenței per Flow (Boxplot)")
    plt.tight_layout()
    fig.savefig(out_dir / "latency_boxplot.png", dpi=160)
    plt.close(fig)

def main():
    out_rows = []
    failures = 0
    total = len(QUESTIONS) * RUNS_PER_QUESTION
    done = 0

    print(f"Benchmark start: {total} cereri către {API_URL}")

    for q in QUESTIONS:
        for i in range(RUNS_PER_QUESTION):
            t0 = time.perf_counter()
            try:
                done += 1
                r = requests.post(API_URL, json={"question": q}, timeout=TIMEOUT_SEC)
                client_ms = (time.perf_counter() - t0) * 1000

                if r.status_code != 200:
                    failures += 1
                    out_rows.append({"question": q, "run": i+1, "status": r.status_code, "client_ms": client_ms, "execution_flow": "ERROR", "server_ms": 0, "answer": "HTTP Error"})
                    continue

                data = r.json()
                flow = data.get("execution_flow", "UNKNOWN")
                server_ms = data.get("latency_ms", 0)
                answer = data.get("answer", "")

                print(f"[{done}/{total}] {flow} | Server: {server_ms}ms")

                out_rows.append({
                    "question": q,
                    "run": i + 1,
                    "http_status": r.status_code,
                    "execution_flow": flow,
                    "server_latency_ms": server_ms,
                    "client_latency_ms": round(client_ms, 2),
                    "answer": answer
                })
            except Exception as e:
                failures += 1
                print(f" Eroare: {e}")

    csv_path = Path("performance_results.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["question", "run", "http_status", "execution_flow", "server_latency_ms", "client_latency_ms", "answer"])
        writer.writeheader()
        writer.writerows(out_rows)

    stats = compute_flow_stats(out_rows)
    out_dir = Path("plots")
    ensure_dir(out_dir)
    write_flow_summary(stats, out_dir / "flow_summary.csv", out_dir / "flow_summary.json")
    
    if PLOT:
        plot_stats(stats, out_rows, out_dir)
        print(f"\nBenchmark finalizat. Graficele sunt în /{out_dir}")

if __name__ == "__main__":
    main()