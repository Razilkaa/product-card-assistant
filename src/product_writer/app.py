from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request

from product_writer.models import Card, Product, Settings
from product_writer.service import GenerationError, generate


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = Settings()
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        app.state.client = client
        yield


app = FastAPI(title="Product Writer", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/cards", response_model=Card)
async def create_card(product: Product, request: Request):
    try:
        return await generate(product, request.app.state.settings, request.app.state.client)
    except GenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
