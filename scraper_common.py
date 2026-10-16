"""Small helpers shared by the two beginner scrapers.

Playwright's sync API is used on purpose: code runs from top to bottom,
which is easier to follow than the async API.
"""

from __future__ import annotations

import logging
import socket
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from playwright.sync_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
)

# Identifies this project. The Chrome-like prefix lets normal pages render,
# and "NewsScraper/1.0" tells the site who is visiting.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 NewsScraper/1.0"
)

# Pause between successful page loads. Waiting for elements is separate:
# this delay is only politeness, so we do not hit the server page after page.
PAGE_DELAY_SECONDS = 2.0
NAVIGATION_TIMEOUT_MS = 30_000
MAX_ATTEMPTS = 3

# One robots.txt download per website, reused for every URL on that site.
_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


def check_robots(url: str) -> bool:
    """Return True when robots.txt allows this URL. Log the reason either way."""
    parser = _robots_for(url)
    if parser is None:
        logging.warning(
            "Skipping %s because robots.txt could not be read, so we will not guess.",
            url,
        )
        return False

    if parser.can_fetch(USER_AGENT, url):
        logging.info("robots.txt allows %s", url)
        return True

    logging.warning(
        "Skipping %s because robots.txt disallows it for our User-Agent.",
        url,
    )
    return False


def _robots_for(url: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[origin]

    robots_url = origin + "/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    # robotparser uses urllib internally; give that download a deadline too.
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(20)
    try:
        parser.read()
    except Exception as exc:
        logging.warning("Could not read %s (%s).", robots_url, exc)
        _ROBOTS_CACHE[origin] = None
        return None
    finally:
        socket.setdefaulttimeout(previous_timeout)

    logging.info("Loaded %s", robots_url)
    _ROBOTS_CACHE[origin] = parser
    return parser


def launch_browser(playwright: Playwright, headed: bool) -> Browser:
    """Open Chromium. Headless (invisible) unless --headed was passed."""
    return playwright.chromium.launch(
        headless=not headed,
        slow_mo=250 if headed else 0,
    )


def new_page(browser: Browser) -> Page:
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="vi-VN",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    page.set_default_timeout(NAVIGATION_TIMEOUT_MS)
    page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    return page


def open_url(page: Page, url: str, wait_selector: str) -> str:
    """Open url and wait until wait_selector exists.

    Returns "ok", "empty" (page loaded but has no matching elements),
    or "failed" after retries.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logging.info("Opening %s (attempt %s/%s)", url, attempt, MAX_ATTEMPTS)
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
            status = response.status if response is not None else 0
            if status >= 400:
                raise PlaywrightError(f"HTTP {status}")

            # Wait for real content instead of sleeping a fixed number of seconds.
            page.wait_for_selector(wait_selector, timeout=NAVIGATION_TIMEOUT_MS)
            return "ok"
        except PlaywrightTimeoutError:
            if _page_looks_empty(page, wait_selector):
                logging.info("No matching elements at %s. Treating it as the last page.", url)
                return "empty"
            logging.warning("Timed out waiting for %s on %s", wait_selector, url)
        except PlaywrightError as exc:
            logging.warning("Could not open %s: %s", url, exc)

        if attempt < MAX_ATTEMPTS:
            # This pause is only between failed attempts, not a substitute
            # for waiting until the element appears.
            time.sleep(PAGE_DELAY_SECONDS)

    logging.error("Gave up on %s after %s attempts.", url, MAX_ATTEMPTS)
    return "failed"


def _page_looks_empty(page: Page, wait_selector: str) -> bool:
    try:
        if page.locator(wait_selector).count() > 0:
            return False
        text = page.locator("body").inner_text(timeout=3_000)
    except PlaywrightError:
        return False
    return len(text.strip()) < 40


def pause_between_pages() -> None:
    logging.info("Waiting %.0f seconds before the next page.", PAGE_DELAY_SECONDS)
    time.sleep(PAGE_DELAY_SECONDS)


def collapse_whitespace(value: str | None) -> str:
    return " ".join((value or "").split())
