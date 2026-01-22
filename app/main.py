import os
import time
import logging
import pyodbc
from typing import List, Optional
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from openai import AsyncAzureOpenAI 
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

clients = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inițializare PaaS/Serverless
    clients["search"] = SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=os.environ["AZURE_SEARCH_INDEX"],
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_ADMIN_KEY"]),
    )
    clients["openai"] = AsyncAzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_CHAT_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_CHAT_ENDPOINT"],
        api_version="2024-12-01-preview",
    )
    # String conexiune SQL (PaaS)
    clients["sql_conn_str"] = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server=tcp:{os.environ['SQL_SERVER']},1433;"
        f"Database={os.environ['SQL_DATABASE']};"
        f"Uid={os.environ['SQL_USER']};"
        f"Pwd={os.environ['SQL_PASSWORD']};"
        f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    yield
    await clients["search"].close()

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    question: str

class Citation(BaseModel):
    source: str
    chunk_id: int

class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation]
    execution_flow: str
    latency_ms: float

def get_sql_data(question: str):
    try:
        with pyodbc.connect(clients["sql_conn_str"]) as conn:
            cursor = conn.cursor()
            q_lower = question.lower()
            
            # 1. Gestionare Ieftin/Scump (Agregări)
            order = None
            if any(word in q_lower for word in ["ieftin", "minim", "mic"]):
                order = "ASC"
            elif any(word in q_lower for word in ["scump", "maxim", "mare"]):
                order = "DESC"
            
            if order:
                cursor.execute(f"""
                    SELECT TOP 1 a.attraction_name, t.price, t.currency, t.ticket_type
                    FROM attractions a
                    JOIN tickets t ON a.attraction_name = t.attraction_name
                    ORDER BY t.price {order}
                """)
                row = cursor.fetchone()
                if row:
                    return f"Informație SQL: {row.attraction_name} are biletul {row.ticket_type} la prețul de {row.price} {row.currency}."

            # 2. Curățare pentru potrivire nume (extragere search_term)
            noise = ["care", "este", "pretul", "prețul", "orarul", "programul", "la", "pentru", "e", "vă rog", "spune-mi"]
            search_term = q_lower
            for w in noise:
                search_term = search_term.replace(f" {w} ", " ").replace(f"{w} ", "")
            search_term = search_term.replace("?", "").strip()

            # 3. Potrivire bidirecțională
            query = """
                SELECT a.attraction_name, h.open_time, h.close_time, t.price, t.currency, t.ticket_type
                FROM attractions a
                LEFT JOIN opening_hours h ON a.attraction_name = h.attraction_name
                LEFT JOIN tickets t ON a.attraction_name = t.attraction_name
                WHERE ? LIKE '%' + a.attraction_name + '%' 
                OR a.attraction_name LIKE ?
            """
            cursor.execute(query, (q_lower, f"%{search_term}%"))
            rows = cursor.fetchall()
            
            if rows:
                data = {}
                for r in rows:
                    if r.attraction_name not in data:
                        data[r.attraction_name] = {"orar": f"{r.open_time}-{r.close_time}", "bilete": []}
                    if r.price:
                        data[r.attraction_name]["bilete"].append(f"{r.ticket_type}: {r.price} {r.currency}")
                
                res_parts = [f"{name} (Orar: {info['orar']}, Bilete: {', '.join(info['bilete'])})" for name, info in data.items()]
                return "\n".join(res_parts)
            return None
    except Exception as e:
        logger.error(f"SQL Error: {e}")
        return None

@app.get("/health")
async def health_check():
    health_status = {"status": "online", "services": {"azure_search": "initialized", "azure_openai": "initialized", "azure_sql": "unknown"}}
    try:
        with pyodbc.connect(clients["sql_conn_str"], timeout=5) as conn:
            health_status["services"]["azure_sql"] = "connected"
    except Exception:
        health_status["services"]["azure_sql"] = "disconnected"
    return health_status

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    start_time = time.perf_counter()
    q_lower = request.question.lower()
    contexts, citations, flow = [], [], []

    # 1. SQL KEYWORDS (Date structurate: prețuri, orar, disponibilitate)
# Include articulări (prețul, biletul) și plural (prețuri, bilete)
    sql_k = [
    "pret", "preț", "pretul", "prețul", "preturi", "prețuri", 
    "bilet", "bilete", "biletul", "biletele", "biletului",
    "costa", "costă", "euro", "ieftin", "ieftină", "scump", "scumpă",
    "maxim", "minim", "mic", "mică", "mare",
    "orar", "orarul", "program", "programul", "funcționare", "vizitare",
    "deschis", "inchis", "închis", "cand", "când", "ora", "ore", "orele",
    "luni", "marti", "marți", "miercuri", "joi", "vineri", "sambata", "sâmbătă", "sâmbăta", "duminica", "duminică",
    "adult", "student", "copil", "copii", "senior", "gratuit", "gratis"
]

# 2. SEARCH KEYWORDS (Context nestructurat: reguli, sfaturi, siguranță, istorie)
# Include termeni care forțează accesarea documentelor PDF/TXT
    search_k = [
    "reguli", "securitate", "vigoare", "safety", "sfat", "sfaturi", "tips", 
    "recomand", "recomanda", "recomandări", "recomandari", "istorie", "detalii",
    "acces", "intrare", "ghid", "transport", "transportul", "metrou", "bus", "autobuz",
    "harta", "hartă", "validare", "validarea", "evita", "cozi", "cozile", 
    "restricții", "restrictii", "călătorie", "calatorie", "cunoască", "cunoasca", 
    "compară", "compara", "îmbarcare", "imbarcare", "vizitarea", "ploaie", "ploioasă"
]

    # 1. Detecție SQL (PaaS)
    is_sql_query = any(k in q_lower for k in sql_k)
    sql_info = None
    if is_sql_query:
        sql_info = get_sql_data(request.question)
        if sql_info:
            contexts.append(f"DATE SQL:\n{sql_info}")
            citations.append(Citation(source="Azure SQL Database", chunk_id=0))
            flow.append("SQL")

    # 2. Verificare Search (Dacă e nevoie de detalii sau SQL nu a găsit nimic)
    needs_search = any(k in q_lower for k in search_k)
    if needs_search or not contexts:
        s_res = await clients["search"].search(search_text=request.question, top=3)
        async for r in s_res:
            contexts.append(f"DOCUMENTE: {r['content']}")
            citations.append(Citation(source=r['source'], chunk_id=r['chunk_id']))
        flow.append("SEARCH")

    # 3. Generare Răspuns LLM
    final_flow = " + ".join(list(dict.fromkeys(flow))) + " + LLM"
    
    response = await clients["openai"].chat.completions.create(
        model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
        messages=[
            {"role": "system", "content": "Ești un asistent de turism pentru Paris. Combină datele SQL (prețuri/orar) cu informațiile din documente. Prioritizează SQL pentru cifre."},
            {"role": "user", "content": f"Context:\n{' '.join(contexts)}\n\nÎntrebare: {request.question}"}
        ],
        temperature=0.2
    )

    latency = (time.perf_counter() - start_time) * 1000
    return ChatResponse(
        answer=response.choices[0].message.content,
        citations=citations,
        execution_flow=final_flow,
        latency_ms=round(latency, 2)
    )
