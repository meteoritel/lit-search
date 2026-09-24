"""共享测试脚手架：完全离线的 urllib 替身。"""

from __future__ import annotations

import io
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import pytest

import litsearch

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeOpener:
    """队列驱动的 urllib opener。元素为 bytes → 200；为 int → 抛对应 HTTPError；
    为异常实例 → 原样抛出（用于模拟连接截断等传输层故障）。"""

    def __init__(self, responses: Sequence[Any]) -> None:
        self.remaining = list(responses)
        self.urls: list[str] = []

    def __call__(self, req: Any, timeout: Any = None) -> FakeResponse:
        self.urls.append(req.full_url)
        if not self.remaining:
            raise AssertionError(f"未预期的额外请求：{req.full_url}")
        item = self.remaining.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, int):
            raise urllib.error.HTTPError(
                req.full_url, item, f"HTTP {item}", {}, io.BytesIO(b"")
            )
        return FakeResponse(item)


@dataclass
class SleepRecorder:
    calls: list[float] = field(default_factory=list)

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_http(responses: Sequence[Any]) -> tuple[litsearch.Http, FakeOpener]:
    opener = FakeOpener(responses)
    return litsearch.Http(opener=opener), opener


@pytest.fixture
def no_sleep() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def config(tmp_path: Path) -> litsearch.Config:
    return litsearch.Config(api_keys={"openalex": "test-key"}, output_dir=tmp_path / "output")


def make_record(**overrides: Any) -> litsearch.Record:
    defaults: dict[str, Any] = {
        "doi": None,
        "title": None,
        "year": 2020,
        "citation_count": 0,
        "authors": [],
        "source": "openalex",
        "sources": ["openalex"],
        "source_id": "x",
    }
    defaults.update(overrides)
    return litsearch.Record(**defaults)


def make_plan(**overrides: Any) -> litsearch.QueryPlan:
    defaults: dict[str, Any] = {"queries": ["q"], "sources": ["openalex"]}
    defaults.update(overrides)
    return litsearch.QueryPlan(**defaults)
