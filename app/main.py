import os
from typing import List
from dotenv import load_dotenv
from pydantic import BaseModel
from fastapi import FastAPI
from openai import AzureOpenAI

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_ADMIN_KEY"]
SEARCH_INDEX = os.environ["AZURE_SEARCH_INDEX"]

AOAI_CHAT_ENDPOINT = os.environ["AZURE_OPENAI_CHAT_ENDPOINT"]
AOAI_CHAT_KEY = os.environ["AZURE_OPENAI_CHAT_KEY"]
AOAI_CHAT_DEPLOYMENT = os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"]

app = FastAPI()


class ChatRequest(BaseModel):
    question: str


class Citation(BaseModel):
    source: str
    chunk_id: int


class ChatResponse(BaseModel):
    answer: str
    citations: List[Citation]


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    question = request.question

    search = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_KEY),
    )

    results = list(search.search(search_text=question, top=5))
    contexts = []
    citations_set = set()
    
    for r in results:
        contexts.append(
            f"- SOURCE: {r.get('source')} | TITLE: {r.get('title')} | chunk_id: {r.get('chunk_id')}\n"
            f"  CONTENT: {r.get('content')}\n"
        )
        citations_set.add((r.get('source'), r.get('chunk_id')))

    aoai = AzureOpenAI(
        api_key=AOAI_CHAT_KEY,
        azure_endpoint=AOAI_CHAT_ENDPOINT,
        api_version="2024-12-01-preview",
    )

    system = (
        "Ești un asistent de turism pentru Paris. Folosește DOAR contextul dat. "
        "Dacă nu ai informația în context, spune că nu apare în date. "
        "Răspunde fără secțiunea 'Citations:' - aceasta va fi adăugată separat."
    )

    user = f"Întrebare: {question}\n\nContext:\n{''.join(contexts)}"

    resp = aoai.chat.completions.create(
        model=AOAI_CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=500,
        temperature=0.2,
    )

    answer = resp.choices[0].message.content
    citations = [Citation(source=source, chunk_id=chunk_id) for source, chunk_id in citations_set]

    return ChatResponse(answer=answer, citations=citations)