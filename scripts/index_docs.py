import os
import glob
import hashlib
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from dotenv import load_dotenv
from openai import AzureOpenAI

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient


load_dotenv()

SEARCH_ENDPOINT = os.environ["AZURE_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["AZURE_SEARCH_ADMIN_KEY"]
SEARCH_INDEX = os.environ["AZURE_SEARCH_INDEX"]

AOAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AOAI_KEY = os.environ["AZURE_OPENAI_API_KEY"]
AOAI_EMBED_DEPLOYMENT = os.environ["AZURE_OPENAI_EMBED_DEPLOYMENT"]  # deployment name
EMBED_DIM = 1536  # text-embedding-3-small


@dataclass
class Chunk:
    source: str
    title: str
    page: int
    chunk_id: int
    content: str


def parse_header(text: str) -> Tuple[str, str, str]:
    # Expected lines like:
    # TITLE: ...
    # SOURCE: ...
    # CITY: ...
    title = ""
    source = ""
    city = ""
    for line in text.splitlines()[:20]:
        if line.startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("SOURCE:"):
            source = line.split(":", 1)[1].strip()
        elif line.startswith("CITY:"):
            city = line.split(":", 1)[1].strip()
    return title, source, city


def chunk_text(text: str, chunk_size: int = 1100, overlap: int = 200) -> List[str]:
    # Simple char-based chunker (good enough for MVP)
    text = text.strip()
    chunks = []
    i = 0
    while i < len(text):
        j = min(len(text), i + chunk_size)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        if j == len(text):
            break
        i = max(0, j - overlap)
    return chunks


def make_id(source: str, chunk_id: int) -> str:
    raw = f"{source}:{chunk_id}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def build_chunks(path: str) -> List[Chunk]:
    raw = open(path, "r", encoding="utf-8").read()
    title, source, city = parse_header(raw)
    if not source:
        # fallback: use filename
        source = os.path.basename(path)

    # Remove header lines from content by skipping first blank-line after header-ish area
    parts = raw.split("\n\n", 1)
    body = parts[1] if len(parts) == 2 else raw

    texts = chunk_text(body, chunk_size=1100, overlap=200)
    out = []
    for idx, t in enumerate(texts):
        out.append(
            Chunk(
                source=source,
                title=title or os.path.basename(path),
                page=0,          # TXT => 0
                chunk_id=idx,
                content=t,
            )
        )
    return out


def embed_texts(client: AzureOpenAI, texts: List[str]) -> List[List[float]]:
    # batch embeddings (Azure OpenAI supports batching; keep batches small)
    vectors: List[List[float]] = []
    batch_size = 16
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        resp = client.embeddings.create(
            model=AOAI_EMBED_DEPLOYMENT,
            input=batch,
        )
        for item in resp.data:
            vec = item.embedding
            if len(vec) != EMBED_DIM:
                raise ValueError(f"Embedding dim mismatch: got {len(vec)} expected {EMBED_DIM}")
            vectors.append(vec)
    return vectors


def main():
    aoai = AzureOpenAI(
        api_key=AOAI_KEY,
        azure_endpoint=AOAI_ENDPOINT,
        api_version="2024-02-01",  # ok for embeddings; if your resource needs another, adjust
    )

    search = SearchClient(
        endpoint=SEARCH_ENDPOINT,
        index_name=SEARCH_INDEX,
        credential=AzureKeyCredential(SEARCH_KEY),
    )

    files = sorted(glob.glob("docs/*.txt"))
    if not files:
        raise RuntimeError("No docs found. Put .txt files in ./docs/")

    all_chunks: List[Chunk] = []
    for f in files:
        all_chunks.extend(build_chunks(f))

    contents = [c.content for c in all_chunks]
    vectors = embed_texts(aoai, contents)

    docs = []
    for c, v in zip(all_chunks, vectors):
        docs.append(
            {
                "id": make_id(c.source, c.chunk_id),
                "content": c.content,
                "source": c.source,
                "page": c.page,
                "chunk_id": c.chunk_id,
                "title": c.title,
                "contentVector": v,
            }
        )

    # Upload in batches
    batch_size = 200
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i+batch_size]
        result = search.upload_documents(documents=batch)
        failed = [r for r in result if not r.succeeded]
        print(f"Uploaded {len(batch)} docs. Failed: {len(failed)}")
        if failed:
            print("Example failure:", failed[0])

    print("Done.")


if __name__ == "__main__":
    main()