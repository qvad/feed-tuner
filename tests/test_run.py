import asyncio
import json
from types import SimpleNamespace

import pytest
import playwright.async_api

from feed_tuner import browser
from feed_tuner.config import Config, ModelConfig
from feed_tuner.model import Post


@pytest.mark.parametrize("apply,timeout,expected", [(False, False, 0), (True, False, 1), (True, True, 0)])
def test_run_caps_and_cleanup(tmp_path, monkeypatch, apply, timeout, expected):
    visited = []
    contexts = []

    class Page:
        def __init__(self):
            self.url = "about:blank"
            self.mouse = SimpleNamespace(wheel=self.wheel)
        async def wheel(self, *args):
            pass
        async def goto(self, url, **kwargs):
            self.url = url
            visited.append(url)
            return SimpleNamespace(status=200)
        def set_default_timeout(self, value):
            pass
        def set_default_navigation_timeout(self, value):
            pass

    class Context:
        def __init__(self):
            self.pages = [Page()]
            self.closed = False
        async def close(self):
            self.closed = True
        async def route(self, *args):
            pass
        def on(self, *args):
            pass

    class Playwright:
        chromium = None
        def __init__(self):
            self.chromium = self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def launch_persistent_context(self, *args, **kwargs):
            assert kwargs["service_workers"] == "block"
            context = Context()
            contexts.append(context)
            return context

    calls = 0
    async def extract(page, platform):
        nonlocal calls
        calls += 1
        return [Post("p0", "https://x.com/space/status/1", "space"), Post("p1", "https://x.com/space/status/2", "more space")]
    async def rank(*args):
        if timeout:
            raise TimeoutError()
        return [(post.id, .9) for post in args[-1]]
    async def noop(*args):
        pass

    monkeypatch.setattr(playwright.async_api, "async_playwright", Playwright)
    monkeypatch.setattr(browser, "extract", extract)
    monkeypatch.setattr(browser, "rank", rank)
    monkeypatch.setattr(browser, "check_page", noop)
    monkeypatch.setattr(browser.asyncio, "sleep", noop)
    config = Config(("space", "stars"), ("x",), ModelConfig("http://localhost/v1", "test"), tmp_path, max_pages=2, max_post_opens=1)
    asyncio.run(browser.run(config, apply=apply, headless=True))
    assert all(context.closed for context in contexts)
    assert sum("/status/" in url for url in visited) == expected
    events = [json.loads(line) for line in next((tmp_path / "logs").glob("*.jsonl")).read_text().splitlines()]
    if timeout:
        assert events[-1]["event"] == "stopped"
    else:
        assert calls == 2
        assert events[-1]["snapshots"] == 2
        # Same canonical posts from the second snapshot are never ranked twice.
        assert len([event for event in events if event["event"] == "rank"]) == 2
        assert events[-1]["opened"] == expected
