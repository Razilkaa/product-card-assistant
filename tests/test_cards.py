import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from product_writer.app import app
from product_writer.models import Product, Settings
from product_writer.service import GenerationError, generate


@pytest.fixture
def draft():
    return {
        "seo_title": "Манометр",
        "seo_description": "Описание прибора. " * 50,
        "search_queries": ["манометр", "купить манометр", "подбор манометра"],
        "commercial": "Для измерения давления согласно описанию.",
    }


def run(product, payload, status=200):
    async def call():
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            return await generate(product, Settings(llm_api_key="test"), client)

    return asyncio.run(call())


def envelope(draft):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}]}


def test_preserves_specs_and_reports_missing(draft):
    result = run(Product(name="Манометр", specs={"Точность": ""}), envelope(draft))
    assert result.specifications["Точность"] == "данных нет"
    assert result.missing_fields == ["manufacturer", "description", "specs.Точность"]


def test_rejects_invented_numbers(draft):
    draft["commercial"] = "Давление 999 МПа"
    with pytest.raises(GenerationError, match="numeric"):
        run(Product(name="Манометр"), envelope(draft))


@pytest.mark.parametrize("status", [401, 429, 500])
def test_upstream_errors(draft, status):
    with pytest.raises(GenerationError):
        run(Product(name="Манометр"), envelope(draft), status)


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{}]}])
def test_broken_response(payload):
    with pytest.raises(GenerationError):
        run(Product(name="Манометр"), payload)


def test_rejects_short_description(draft):
    draft["seo_description"] = "Коротко"
    with pytest.raises(GenerationError):
        run(Product(name="Манометр"), envelope(draft))


@pytest.mark.parametrize("body", ["{", '{"name":"  "}', '{"name":"x","specs":[]}'])
def test_invalid_input(body):
    with TestClient(app) as client:
        assert (
            client.post(
                "/cards", content=body, headers={"Content-Type": "application/json"}
            ).status_code
            == 422
        )


def test_health():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_timeout():
    async def call():
        def fail(request):
            raise httpx.ReadTimeout("secret upstream detail")

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with pytest.raises(GenerationError, match="timed out"):
                await generate(Product(name="x"), Settings(llm_api_key="test"), client)

    asyncio.run(call())


def test_examples_follow_contract():
    from pathlib import Path

    from product_writer.models import Card

    for path in Path("examples").glob("*.output.json"):
        card = Card.model_validate_json(path.read_text(encoding="utf-8"))
        assert card.status == "draft_requires_review"


def test_missing_key():
    async def call():
        async with httpx.AsyncClient() as client:
            with pytest.raises(GenerationError, match="not configured"):
                await generate(Product(name="x"), Settings(llm_api_key=""), client)

    asyncio.run(call())


def test_truncated_output(draft):
    payload = envelope(draft)
    payload["choices"][0]["finish_reason"] = "length"
    with pytest.raises(GenerationError, match="incomplete"):
        run(Product(name="x"), payload)
