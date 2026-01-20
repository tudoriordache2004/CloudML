import os
from dotenv import load_dotenv
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

def main():
    question = os.environ.get("QUESTION", "Ce pot vizita în Paris într-o zi ploioasă?")

    search = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_KEY),
    )

    results = list(search.search(search_text=question, top=5))
    contexts = []
    for r in results:
        contexts.append(
            f"- SOURCE: {r.get('source')} | TITLE: {r.get('title')} | chunk_id: {r.get('chunk_id')}\n"
            f"  CONTENT: {r.get('content')}\n"
        )

    aoai = AzureOpenAI(
        api_key=AOAI_CHAT_KEY,
        azure_endpoint=AOAI_CHAT_ENDPOINT,
        api_version="2024-12-01-preview",
    )

    system = (
        "Ești un asistent de turism pentru Paris. Folosește DOAR contextul dat. "
        "Dacă nu ai informația în context, spune că nu apare în date. "
        "La final, include o secțiune 'Citations:' cu lista de (source, chunk_id)."
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

    print(resp.choices[0].message.content)

if __name__ == "__main__":
    main()