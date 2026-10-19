import asyncio
import pytest
from playwright.async_api import async_playwright, Error
from feed_tuner.browser import guard_context


def test_real_browser_blocks_external_navigation():
    async def scenario():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(service_workers='block')
                page = await context.new_page()
                failed = []
                page.on('requestfailed', lambda request: failed.append(request.failure))
                await guard_context(context, 'x')
                with pytest.raises(Error, match='ERR_FAILED'):
                    await page.goto('https://outside.invalid/steal', timeout=5000)
                assert failed
                assert page.url == 'about:blank'
            finally:
                await browser.close()
    asyncio.run(scenario())
