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
        def respond(request):
            body = json.loads(request.content)
            verifying = "product" in json.loads(body["messages"][1]["content"])
            result = envelope({"supported": True}) if verifying else payload
            return httpx.Response(status, json=result)

        transport = httpx.MockTransport(respond)
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


@pytest.mark.parametrize("choice", [None, "invalid", 1, []])
def test_invalid_choice_type(choice):
    with pytest.raises(GenerationError, match="invalid response"):
        run(Product(name="Манометр"), {"choices": [choice]})


@pytest.mark.parametrize(
    "specs",
    [
        {"Давление": "1", " Давление ": "2"},
        {"Производитель": "B"},
    ],
)
def test_rejects_specification_collisions(specs):
    with TestClient(app) as client:
        response = client.post(
            "/cards",
            json={
                "name": "Манометр",
                "manufacturer": "A",
                "specs": specs,
            },
        )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "claim",
    [
        "Прибор водонепроницаемый и сертифицирован для взрывоопасных сред.",
        "Диапазон давления 100 МПа",
    ],
)
def test_rejects_unsupported_claims(draft, claim):
    draft["commercial"] = claim

    async def call():
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return httpx.Response(200, json=envelope(draft))
            return httpx.Response(200, json=envelope({"supported": False}))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(GenerationError, match="unsupported facts"):
                await generate(
                    Product(name="Манометр", specs={"Диаметр": "100 мм"}),
                    Settings(llm_api_key="test"),
                    client,
                )
        assert len(requests) == 2
        evidence = json.loads(requests[1]["messages"][1]["content"])
        assert evidence["product"]["specs"] == {"Диаметр": "100 мм"}
        assert evidence["draft"]["commercial"] == claim

    asyncio.run(call())


@pytest.mark.parametrize(
    "verdict", [None, {}, {"supported": "true"}, {"supported": 1}, {"supported": True, "extra": 1}]
)
def test_invalid_verification_fails_closed(draft, verdict):
    async def call():
        responses = iter([envelope(draft), envelope(verdict)])
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(GenerationError, match="invalid verification"):
                await generate(Product(name="Манометр"), Settings(llm_api_key="test"), client)

    asyncio.run(call())


@pytest.mark.parametrize("failure", ["timeout", "http", "json"])
def test_verification_failure(draft, failure):
    async def call():
        calls = 0

        def respond(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, json=envelope(draft))
            if failure == "timeout":
                raise httpx.ReadTimeout("verification timeout")
            if failure == "http":
                return httpx.Response(503)
            return httpx.Response(200, text="invalid json")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(GenerationError):
                await generate(Product(name="Манометр"), Settings(llm_api_key="test"), client)
        assert calls == 2

    asyncio.run(call())


def test_preserves_nonconflicting_specs(draft):
    result = run(
        Product(
            name="Манометр", manufacturer="A", specs={" Давление ": " 1 МПа ", "Материал": "Сталь"}
        ),
        envelope(draft),
    )
    assert result.specifications == {"Производитель": "A", "Давление": "1 МПа", "Материал": "Сталь"}
