import os
import logging
from typing import List
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

@app.get("/health")
async def health_check():
    return {"status": "online"}

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # 1. Search (Async)
        search_results = await clients["search"].search(search_text=request.question, top=5)
        
        contexts = []
        citations = []
        async for r in search_results:
            contexts.append(
                f"- SOURCE: {r.get('source')} | chunk_id: {r.get('chunk_id')}\n"
                f"  CONTENT: {r.get('content')}\n"
            )
            citations.append(Citation(source=r.get('source'), chunk_id=r.get('chunk_id')))

        # 2. LLM Call (Async)
        system_prompt = (
            "Ești un asistent de turism pentru Paris. Folosește DOAR contextul dat. "
            "Dacă nu ai informația în context, spune că nu apare în date."
        )
        user_prompt = f"Întrebare: {request.question}\n\nContext:\n{''.join(contexts)}"

        response = await clients["openai"].chat.completions.create(
            model=os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2
        )

        return ChatResponse(
            answer=response.choices[0].message.content,
            citations=citations
        )
    except Exception as e:
        logger.error(f"Error in chat endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")