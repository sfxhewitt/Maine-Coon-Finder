"""Checks the Claude request is well formed and the answer is parsed, using a
fake HTTP transport (no API key or network needed)."""

import json

import anthropic
import httpx2 as httpx
from conftest import FakeHttp

from maine_coon_finder.config import Settings
from maine_coon_finder.models import Listing
from maine_coon_finder.vision import ColourChecker

ANSWER = {
    "dark_grey_kitten_present": True,
    "colour_seen": "blue smoke",
    "confidence": 0.86,
    "looks_like_maine_coon": True,
    "is_crossbreed": False,
    "grey_kitten_still_available": None,
    "age_weeks": 10,
    "vaccinated": True,
    "wormed": True,
    "flea_treated": None,
    "vet_checked": True,
    "microchipped": None,
    "registered": None,
    "best_image_index": 1,
    "reason": "Second photo shows a charcoal kitten with a pale undercoat.",
    "summary": "Blue smoke boy, 10 weeks.",
}


def test_assess_builds_request_and_parses_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": json.dumps(ANSWER)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    s = Settings(anthropic_api_key="sk-test", max_images_per_check=2)
    checker = ColourChecker(s, FakeHttp({}))
    checker.client = anthropic.Anthropic(api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    l = Listing(source="gumtree", source_id="1", url="u", title="Maine coon", description="blue boy",
                images=["https://a/1.jpg", "https://a/2.jpg", "https://a/3.jpg"])
    a, used = checker.assess(l)

    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    imgs = [b for b in body["messages"][0]["content"] if b["type"] == "image"]
    assert len(imgs) == 2  # capped by max_images_per_check
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"].get("anthropic-beta", "")
    assert a.is_match and a.colour == "blue smoke" and a.best_image_index == 1
    assert a.vaccinated is True and a.age_weeks == 10
    assert used == ["https://a/1.jpg", "https://a/2.jpg"]
