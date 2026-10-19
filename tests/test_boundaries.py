import asyncio
import json
from pathlib import Path

import httpx
import pytest

from feed_tuner.browser import canonical_post, extract, permitted, check_page, StopBrowsing
from feed_tuner.config import ConfigError, endpoint, load
from feed_tuner.model import Post, ModelConfig, rank, validate_ranking


@pytest.mark.parametrize("value", ["https://evil.test/a/status/1", "http://x.com/a/status/1", "https://x.com.evil.test/a/status/1", "https://user@x.com/a/status/1", "https://x.com:443/a/status/1", "https://x.com/a/status/1/photo/1", "javascript:alert(1)", "https://x.com/a/status/1%2fanything"])
def test_reject_untrusted_urls(value):
    assert canonical_post("x", value) is None


def test_canonical_post_and_navigation():
    assert canonical_post("x", "https://x.com/user/status/123?s=20#reply") == "https://x.com/user/status/123"
    assert canonical_post("threads", "https://threads.com/@name/post/ABC_1/?x=1") == "https://www.threads.com/@name/post/ABC_1"
    assert canonical_post("threads", "https://threads.com/@name/post/ABC/other") is None
    assert permitted("threads", "https://www.threads.com/search?q=test")
    assert not permitted("x", "https://x.com:443/search")
    assert not permitted("x", "https://x.com.evil.test/search")


@pytest.mark.parametrize("raw", ['[{"id":"p99","score":1}]', '[{"id":"p0","score":true}]', '[{"id":"p0","score":NaN}]', '[{"id":"p0","score":1,"url":"https://evil.test"}]', '[{"id":"p0","score":2}]', '[{"id":"p0","score":1},{"id":"p0","score":1}]', '{}', '```json\n[]\n```'])
def test_reject_model_control(raw):
    with pytest.raises(ValueError):
        validate_ranking(raw, {"p0", "p1"})


def test_rank_valid():
    assert validate_ranking('[{"id":"p0","score":0.2},{"id":"p1","score":0.9}]', {"p0", "p1"}) == [("p1", .9), ("p0", .2)]


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:secret@example.com/v1", "https://example.com/v1?q=secret", "file:///tmp/a", "http://127.0.0.1.evil.test/v1"])
def test_model_endpoint_rejects(url):
    with pytest.raises(ConfigError):
        endpoint(url)


def test_model_endpoint_allows_loopback_and_https():
    for url in ["http://localhost:11434/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1", "https://api.example.com/v1"]:
        assert endpoint(url) == url


@pytest.mark.parametrize("setting", ['topics = [" "]', 'max_minutes = nan', 'max_pages = 1.5', 'max_post_opens = -1', 'pause_seconds = inf', 'platforms = ["other"]'])
def test_invalid_config(tmp_path, setting):
    source = Path("config.example.toml").read_text()
    key = setting.split("=", 1)[0].strip()
    lines = [line for line in source.splitlines() if not line.startswith(key + " =")]
    lines.insert(0, setting)
    target = tmp_path / "config.toml"
    target.write_text("\n".join(lines))
    with pytest.raises(ConfigError):
        load(target)


def test_example_config():
    config = load(Path("config.example.toml"))
    assert config.max_pages == 6
    assert config.min_score == .7


def test_mock_provider_only_receives_snippets(monkeypatch):
    def handle(request):
        payload = json.loads(request.content)
        assert payload["model"] == "test"
        assert "https://x.com" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": '[{"id":"p0","score":0.9}]'}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(rank(ModelConfig("http://localhost:8000/v1", "test"), ("space",), [Post("p0", "https://x.com/a/status/1", "astronomy")]))
    assert result == [("p0", .9)]


def test_offline_browser_extraction_and_checkpoint():
    from playwright.async_api import async_playwright
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body='''<html><body><main>
                    <article>Space telescope discovery <a href="https://x.com/astro/status/123?s=20">date</a><a href="https://evil.test/astro/status/999">evil</a></article>
                    <article>Duplicate <a href="https://x.com/astro/status/123">date</a></article>
                    <section><div><div>Astronomy thread with stars and planets <a href="https://www.threads.com/@astro/post/ABC_1">time</a></div></div></section>
                    <article>Unrelated content outside the neighborhood</article>
                    </main></body></html>'''))
                await page.goto("https://x.com/search?q=space")
                await check_page(page, "x")
                posts = await extract(page, "x")
                assert len(posts) == 1
                assert posts[0].url == "https://x.com/astro/status/123"
                assert "Space telescope" in posts[0].text
                threads = await extract(page, "threads")
                assert len(threads) == 1
                assert "Astronomy thread" in threads[0].text
                assert "Unrelated" not in threads[0].text
                await page.goto("https://x.com/i/flow/login")
                with pytest.raises(StopBrowsing):
                    await check_page(page, "x")
            finally:
                await browser.close()
    asyncio.run(scenario())


def test_reject_truncated_provider_response(monkeypatch):
    def handle(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "[]"}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(ValueError):
        asyncio.run(rank(ModelConfig("http://localhost/v1", "test"), ("space",), [Post("p0", "https://x.com/a/status/1", "space")]))
