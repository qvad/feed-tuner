from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
from urllib.parse import quote, urlsplit
import uuid

from .config import Config
from .model import Post, rank

HOSTS = {"x": "x.com", "threads": "www.threads.com"}


class StopBrowsing(RuntimeError):
    pass


def canonical_post(platform: str, value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None:
            return None
        if platform == "x" and parsed.hostname == "x.com" and re.fullmatch(r"/[A-Za-z0-9_]{1,15}/status/[0-9]+/?", parsed.path):
            return "https://x.com" + parsed.path.rstrip("/")
        if platform == "threads" and parsed.hostname in {"threads.com", "www.threads.com"} and re.fullmatch(r"/@[A-Za-z0-9_.]+/post/[A-Za-z0-9_-]+/?", parsed.path):
            return "https://www.threads.com" + parsed.path.rstrip("/")
    except (ValueError, TypeError):
        pass
    return None


def permitted(platform: str, value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hosts = {HOSTS[platform]} | ({"threads.com"} if platform == "threads" else set())
        return parsed.scheme == "https" and parsed.hostname in hosts and parsed.port is None and not parsed.username and not parsed.password
    except ValueError:
        return False


async def extract(page, platform: str) -> list[Post]:
    rows = await page.evaluate(r"""(platform) => {
        const anchors = [...document.querySelectorAll('a[href]')];
        return anchors.filter(a => platform === 'x' ? /\/status\/\d+/.test(a.pathname) : /\/@[^/]+\/post\/[^/]+/.test(a.pathname)).slice(0, 300).map(a => {
            // Threads has unstable markup: collect only a small anchor neighborhood.
            let node = a.closest('article');
            if (!node && platform === 'threads') {
                node = a.parentElement;
                for (let i=0; i<3 && node && (node.innerText || '').length < 80; i++) {
                    const parent = node.parentElement;
                    if (!parent || ['BODY','HTML','MAIN'].includes(parent.tagName) || parent.querySelectorAll('a[href*="/post/"]').length > 6) break;
                    node = parent;
                }
            }
            return {url: a.href, text: ((node || a).innerText || '').slice(0, 2000)};
        });
    }""", platform)
    unique = {}
    for row in rows:
        url = canonical_post(platform, row["url"])
        if url and row["text"].strip():
            unique.setdefault(url, row["text"].strip())
    return [Post(f"p{i}", url, snippet) for i, (url, snippet) in enumerate(list(unique.items())[:40])]


async def check_page(page, platform: str) -> None:
    if not permitted(platform, page.url):
        raise StopBrowsing("Navigation left the selected platform; stopped")
    path = urlsplit(page.url).path.lower()
    if any(part in path for part in ("/login", "/i/flow", "/checkpoint", "/challenge", "/accounts/")):
        raise StopBrowsing("Login or account checkpoint detected; use the manual login command")
    text = (await page.locator("body").inner_text(timeout=5000))[:12000].lower()
    phrases = ("rate limit exceeded", "too many requests", "try again later", "verify you are human", "confirm you're human", "unusual activity", "temporarily restricted", "log in to search", "log in to continue", "sign in to continue")
    if any(phrase in text for phrase in phrases):
        raise StopBrowsing("Login, checkpoint, or rate limit detected; stopped without retry")
    # A visible password field or login form is a stronger signal than post text.
    if await page.locator('input[type="password"]:visible, input[autocomplete="username"]:visible').count():
        raise StopBrowsing("Login form detected; use the manual login command")


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


class Log:
    def __init__(self, directory: Path):
        private_dir(directory)
        self.path = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".jsonl")
        self.stream = os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")

    def write(self, event: str, **fields):
        self.stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, ensure_ascii=False) + "\n")
        self.stream.flush()

    def close(self):
        self.stream.close()


async def guard_context(context, platform: str):
    async def route_handler(route):
        request = route.request
        if request.is_navigation_request() and not permitted(platform, request.url):
            await route.abort()
        else:
            await route.continue_()
    await context.route("**/*", route_handler)
    # Popups are never part of the run.
    context.on("page", lambda page: asyncio.create_task(page.close()))


async def run(config: Config, apply: bool = False, headless: bool = False):
    from playwright.async_api import async_playwright
    private_dir(config.data_dir)
    log = Log(config.data_dir / "logs")
    opened = 0
    snapshots = 0
    seen: set[str] = set()
    context = None
    log.write("start", apply=apply, max_minutes=config.max_minutes)
    print(f"Log: {log.path}", flush=True)
    try:
        async with async_playwright() as playwright:
            async with asyncio.timeout(config.max_minutes * 60):
                for platform in config.platforms:
                    profile = config.data_dir / "profiles" / platform
                    private_dir(profile)
                    context = await playwright.chromium.launch_persistent_context(str(profile), headless=headless, timeout=20000, service_workers="block")
                    page = context.pages[0] if context.pages else await context.new_page()
                    await guard_context(context, platform)
                    page.set_default_timeout(10000)
                    page.set_default_navigation_timeout(20000)
                    try:
                        for topic in config.topics:
                            if snapshots >= config.max_pages:
                                break
                            query = quote(topic, safe="")
                            url = f"https://x.com/search?q={query}&f=live" if platform == "x" else f"https://www.threads.com/search?q={query}"
                            response = await page.goto(url, wait_until="domcontentloaded")
                            if response and response.status in (401, 403, 429):
                                raise StopBrowsing(f"Platform returned HTTP {response.status}; stopped")
                            await asyncio.sleep(config.pause_seconds)
                            await check_page(page, platform)
                            # Two bounded search snapshots per topic, including a scroll.
                            for _ in range(2):
                                if snapshots >= config.max_pages:
                                    break
                                snapshots += 1
                                posts = [post for post in await extract(page, platform) if post.url not in seen]
                                seen.update(post.url for post in posts)
                                ranking = await rank(config.model, config.topics, posts)
                                candidates = {post.id: post for post in posts}
                                log.write("search", platform=platform, topic=topic, candidates=len(posts), snapshot=snapshots)
                                for ident, score in ranking:
                                    post = candidates[ident]
                                    log.write("rank", platform=platform, url=post.url, score=score, selected=score >= config.min_score)
                                    if not apply or score < config.min_score or opened >= config.max_post_opens:
                                        continue
                                    response = await page.goto(post.url, wait_until="domcontentloaded")
                                    if response and response.status in (401, 403, 429):
                                        raise StopBrowsing(f"Platform returned HTTP {response.status}; stopped")
                                    opened += 1
                                    await asyncio.sleep(config.pause_seconds)
                                    await check_page(page, platform)
                                    log.write("opened", platform=platform, url=post.url)
                                    await asyncio.sleep(config.pause_seconds + random.uniform(0, 1))
                                    await page.goto(url, wait_until="domcontentloaded")
                                    await asyncio.sleep(config.pause_seconds)
                                    await check_page(page, platform)
                                await page.mouse.wheel(0, random.randint(450, 750))
                                await asyncio.sleep(config.pause_seconds + random.uniform(0, 1))
                                await check_page(page, platform)
                    finally:
                        await context.close()
                        context = None
                    if snapshots >= config.max_pages:
                        break
            log.write("complete", snapshots=snapshots, opened=opened)
    except TimeoutError:
        log.write("stopped", reason="Runtime limit reached", snapshots=snapshots, opened=opened)
        print("Runtime limit reached; browser closed.", flush=True)
    except Exception as exc:
        log.write("stopped", reason=type(exc).__name__, snapshots=snapshots, opened=opened)
        raise
    finally:
        if context:
            await context.close()
        log.close()


async def login(config: Config, platform: str):
    from playwright.async_api import async_playwright
    profile = config.data_dir / "profiles" / platform
    private_dir(profile)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(str(profile), headless=False, timeout=20000, service_workers="block")
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://" + HOSTS[platform], wait_until="domcontentloaded", timeout=20000)
            print("Complete login manually in the browser, then close the browser window. The login command closes the browser after 10 minutes.", flush=True)
            async with asyncio.timeout(600):
                while context.pages:
                    await asyncio.sleep(1)
        finally:
            await context.close()
