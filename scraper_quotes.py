"""Practice scraper for the JavaScript version of Quotes to Scrape.

The /js/ pages start empty. A browser has to run JavaScript before any
quote appears, which is why this script uses Playwright instead of requests.

Run:
    python scraper_quotes.py
    python scraper_quotes.py --pages 2
    python scraper_quotes.py --headed
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
from playwright.sync_api import sync_playwright

from scraper_common import (
    check_robots,
    collapse_whitespace,
    launch_browser,
    new_page,
    open_url,
    pause_between_pages,
)

START_URL = "https://quotes.toscrape.com/js/"
OUTPUT_PATH = Path(__file__).resolve().parent / "sample_output" / "quotes_js.csv"
QUOTE_SELECTOR = "div.quote"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect quotes from quotes.toscrape.com/js/ with Playwright."
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=3,
        help="How many listing pages to follow (default: 3).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window instead of running headless.",
    )
    return parser.parse_args()


def scrape_quotes(max_pages: int, headed: bool) -> pd.DataFrame:
    """Walk the Next link and collect text, author, and tags."""
    if max_pages < 1:
        raise SystemExit("--pages must be at least 1.")

    rows: list[dict[str, str]] = []
    url = START_URL

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headed)
        try:
            page = new_page(browser)
            for page_number in range(1, max_pages + 1):
                # Check every URL, including pages reached from the Next link.
                if not check_robots(url):
                    break

                status = open_url(page, url, QUOTE_SELECTOR)
                if status != "ok":
                    break

                quotes = page.locator(QUOTE_SELECTOR)
                logging.info("Page %s has %s quotes.", page_number, quotes.count())
                for index in range(quotes.count()):
                    card = quotes.nth(index)
                    tags = [
                        collapse_whitespace(tag)
                        for tag in card.locator("a.tag").all_text_contents()
                    ]
                    rows.append(
                        {
                            "text": collapse_whitespace(
                                card.locator("span.text").text_content()
                            ),
                            "author": collapse_whitespace(
                                card.locator("small.author").text_content()
                            ),
                            "tags": "; ".join(tag for tag in tags if tag),
                        }
                    )

                if page_number >= max_pages:
                    break

                next_link = page.locator("li.next a")
                if next_link.count() == 0:
                    logging.info("No Next link on this page. Stopping.")
                    break

                href = next_link.first.get_attribute("href")
                if not href:
                    logging.info("Next link has no href. Stopping.")
                    break

                url = urljoin(page.url, href)
                pause_between_pages()
        finally:
            browser.close()

    return pd.DataFrame(rows, columns=["text", "author", "tags"])


def main() -> None:
    args = parse_args()
    frame = scrape_quotes(args.pages, args.headed)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig adds a BOM so Excel on Windows opens Vietnamese text correctly.
    frame.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    logging.info("Saved %s quotes to %s", len(frame), OUTPUT_PATH)
    if frame.empty:
        logging.warning("No quotes were saved.")
        return
    print(frame.to_string(index=False))


if __name__ == "__main__":
    # Vietnamese quote text needs a UTF-8 console on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
