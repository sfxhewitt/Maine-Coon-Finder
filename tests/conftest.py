from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, text: str = "", content: bytes = b"", ctype: str = "text/html"):
        self.text = text
        self.content = content or text.encode()
        self.headers = {"content-type": ctype}
        self.status_code = 200

    def json(self):
        import json

        return json.loads(self.text)


class FakeHttp:
    """Maps URL substrings to fixture files."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        for needle, fname in self.routes.items():
            if needle in url:
                return FakeResponse((FIXTURES / fname).read_text(encoding="utf-8"))
        raise AssertionError(f"unexpected URL {url}")

    def get_bytes(self, url: str, max_bytes: int = 0):
        return b"\xff\xd8fakejpeg", "image/jpeg"


@pytest.fixture
def fixtures_dir():
    return FIXTURES
