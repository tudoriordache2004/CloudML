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
            # Interogare bazată pe tabelele tale: attractions, opening_hours, tickets
            query = """
                SELECT TOP 1 a.attraction_name, h.open_time, h.close_time, t.price, t.currency
                FROM attractions a
                JOIN opening_hours h ON a.attraction_name = h.attraction_name
                JOIN tickets t ON a.attraction_name = t.attraction_name
                WHERE ? LIKE '%' + a.attraction_name + '%'
            """
            cursor.execute(query, (question,))
            row = cursor.fetchone()
            if row:
                return {
                    "text": f"Date SQL: {row.attraction_name} | Orar: {row.open_time}-{row.close_time} | Pret: {row.price} {row.currency}",
                    "source": "Azure SQL (PaaS)"
                }
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
    flow_type = ""
    contexts = []
    citations = []
    
    try:
        # 1. Verificare cuvinte cheie SQL
        sql_keywords = ["pret", "preț", "bilet", "ticket", "price", "orar", "hours", "open", "deschis"]
        is_sql_query = any(k in request.question.lower() for k in sql_keywords)
        
        # 2. Execuție Flow SQL + LLM
        if is_sql_query:
            sql_result = get_sql_data(request.question)
            if sql_result:
                contexts.append(sql_result["text"])
                citations.append(Citation(source=sql_result["source"], chunk_id=0))
                flow_type = "SQL + LLM"

        # 3. Execuție Flow Search + LLM (dacă SQL nu a fost declanșat sau nu a găsit nimic)
        if not contexts:
            search_results = await clients["search"].search(search_text=request.question, top=5)
            async for r in search_results:
                contexts.append(f"Content: {r.get('content')}")
                citations.append(Citation(source=r.get('source'), chunk_id=r.get('chunk_id')))
            flow_type = "Search + LLM"

        # 4. Apel LLM
        response = await clients["openai"].chat.completions.create(
            model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
            messages=[
                {"role": "system", "content": "Ești un asistent de turism. Folosește contextul oferit."},
                {"role": "user", "content": f"Context: {' '.join(contexts)}\nIntrebare: {request.question}"}
            ],
            temperature=0.2
        )

        latency = (time.perf_counter() - start_time) * 1000
        
        # LOGARE ÎN CONSOLĂ
        logger.info(f">>> FLOW DETECTED: {flow_type} | Latency: {latency:.2f}ms")

        return ChatResponse(
            answer=response.choices[0].message.content,
            citations=citations,
            execution_flow=flow_type,
            latency_ms=round(latency, 2)
        )
        
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Internal Error")