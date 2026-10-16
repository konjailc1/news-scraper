"""Collect CafeF stock-market headlines for personal investment monitoring.

Listing used:
    https://cafef.vn/thi-truong-chung-khoan.chn

That is CafeF's "Thị trường chứng khoán" section (the CHỨNG KHOÁN menu).
Older headlines are loaded from /timelinelist/{zone}/{page}.chn, which is
the same "next page" request the website makes when you scroll.

Only listing fields are saved: title, URL, publish time, a short sapo,
and the category. Article pages are never opened, so full article text
is not copied.

Run:
    python scraper_cafef.py
    python scraper_cafef.py --max-articles 10
    python scraper_cafef.py --keywords "HPG,MBS,SHB"
    python scraper_cafef.py --headed
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
from playwright.sync_api import Page, sync_playwright

from scraper_common import (
    check_robots,
    collapse_whitespace,
    launch_browser,
    new_page,
    open_url,
    pause_between_pages,
)

LISTING_URL = "https://cafef.vn/thi-truong-chung-khoan.chn"
SITE_ORIGIN = "https://cafef.vn"
# Fallback if the page does not expose the hidden zone id input.
DEFAULT_ZONE_ID = "18831"
DEFAULT_CATEGORY = "Thị trường chứng khoán"
# Safety stop so a rare keyword cannot walk the whole archive.
MAX_LISTING_PAGES = 6
SUMMARY_LIMIT = 300

OUTPUT_DIR = Path(__file__).resolve().parent / "sample_output"
# Highlight block on page 1, plus the main headline stream.
# The stream is split across more than one box, so match every tlitem
# inside the news column. "Đọc nhiều" and sponsored side boxes stay out.
FIRST_PAGE_SELECTOR = (
    "div.list-focus-main div[role='article'], "
    "div.list-news-main div.tlitem.box-category-item"
)
NEXT_PAGE_SELECTOR = "div.tlitem.box-category-item"
COLUMNS = ["title", "url", "publish_time", "summary", "category"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect CafeF stock-market headlines (metadata only)."
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=30,
        help="Stop after this many kept headlines (default: 30).",
    )
    parser.add_argument(
        "--keywords",
        default="",
        help='Optional comma-separated words. Example: "HPG,MBS,SHB".',
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window instead of running headless.",
    )
    return parser.parse_args()


def parse_keywords(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def title_matches(title: str, keywords: list[str]) -> bool:
    """True when the headline contains one of the keywords as a whole token."""
    if not keywords:
        return True
    for keyword in keywords:
        pattern = rf"(?<![0-9A-Za-z]){re.escape(keyword)}(?![0-9A-Za-z])"
        if re.search(pattern, title, flags=re.IGNORECASE):
            return True
    return False


def limit_summary(text: str) -> str:
    """Keep the on-page sapo short. This is not the article body."""
    if len(text) <= SUMMARY_LIMIT:
        return text
    return text[: SUMMARY_LIMIT - 3].rstrip() + "..."


def read_category(page: Page) -> str:
    heading = page.locator("h1.zone-title")
    if heading.count() == 0:
        return DEFAULT_CATEGORY
    name = collapse_whitespace(heading.first.text_content())
    return name or DEFAULT_CATEGORY


def read_zone_id(page: Page) -> str:
    zone = page.locator("#hdZoneId")
    if zone.count() == 0:
        return DEFAULT_ZONE_ID
    value = collapse_whitespace(zone.first.get_attribute("value"))
    return value or DEFAULT_ZONE_ID


def normalize_time(value: str) -> str:
    """Turn 2026-10-16T15:59:00 into the same style CafeF prints: 16/10/2026 - 15:59."""
    match = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?",
        value,
    )
    if not match:
        return value
    year, month, day, hour, minute = match.groups()
    return f"{day}/{month}/{year} - {hour}:{minute}"


def read_publish_time(card) -> str:
    clock = card.locator("p.time, span.time")
    if clock.count() == 0:
        return ""
    # The title attribute is the absolute time. Visible text is sometimes
    # the same clock, and sometimes a relative label such as "2 giờ trước".
    title = normalize_time(collapse_whitespace(clock.first.get_attribute("title")))
    shown = normalize_time(collapse_whitespace(clock.first.text_content()))
    if re.search(r"\d{2}/\d{2}/\d{4}", title):
        return title
    if re.search(r"\d{2}/\d{2}/\d{4}", shown):
        return shown
    return title or shown


def extract_cards(page: Page, selector: str, category: str) -> list[dict[str, str]]:
    """Read headline cards already on the listing. Do not open each article."""
    cards = page.locator(selector)
    found: list[dict[str, str]] = []
    for index in range(cards.count()):
        card = cards.nth(index)
        link = card.locator("h2 a, h3 a")
        if link.count() == 0:
            continue
        href = link.first.get_attribute("href") or ""
        title = collapse_whitespace(link.first.get_attribute("title"))
        if not title:
            title = collapse_whitespace(link.first.text_content())
        if not href or not title:
            continue

        sapo = card.locator("p.sapo")
        # text_content includes the sapo even when CSS hides it on featured cards.
        summary = ""
        if sapo.count():
            summary = limit_summary(collapse_whitespace(sapo.first.text_content()))

        found.append(
            {
                "title": title,
                "url": urljoin(SITE_ORIGIN, href),
                "publish_time": read_publish_time(card),
                "summary": summary,
                "category": category,
            }
        )
    return found


def add_matches(
    rows: list[dict[str, str]],
    seen: set[str],
    cards: list[dict[str, str]],
    keywords: list[str],
    max_articles: int,
) -> tuple[int, int]:
    """Append new matching headlines. Returns (kept, skipped)."""
    kept = 0
    skipped = 0
    for card in cards:
        if len(rows) >= max_articles:
            break
        if card["url"] in seen:
            continue
        seen.add(card["url"])
        if not title_matches(card["title"], keywords):
            skipped += 1
            continue
        rows.append(card)
        kept += 1
    return kept, skipped


def scrape_cafef(max_articles: int, keywords: list[str], headed: bool) -> pd.DataFrame:
    if max_articles < 1:
        raise SystemExit("--max-articles must be at least 1.")

    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headed)
        try:
            page = new_page(browser)
            if not check_robots(LISTING_URL):
                return pd.DataFrame(columns=COLUMNS)

            status = open_url(page, LISTING_URL, FIRST_PAGE_SELECTOR)
            if status != "ok":
                return pd.DataFrame(columns=COLUMNS)

            category = read_category(page)
            zone_id = read_zone_id(page)
            logging.info("Category: %s. Zone id: %s.", category, zone_id)

            kept, skipped = add_matches(
                rows,
                seen,
                extract_cards(page, FIRST_PAGE_SELECTOR, category),
                keywords,
                max_articles,
            )
            logging.info("Page 1: kept %s, skipped %s.", kept, skipped)

            # Page 1 is the normal section URL. Later pages are the site's
            # own timeline endpoint, still checked against robots.txt.
            for page_number in range(2, MAX_LISTING_PAGES + 1):
                if len(rows) >= max_articles:
                    break
                pause_between_pages()
                next_url = f"{SITE_ORIGIN}/timelinelist/{zone_id}/{page_number}.chn"
                if not check_robots(next_url):
                    break
                status = open_url(page, next_url, NEXT_PAGE_SELECTOR)
                if status != "ok":
                    break
                kept, skipped = add_matches(
                    rows,
                    seen,
                    extract_cards(page, NEXT_PAGE_SELECTOR, category),
                    keywords,
                    max_articles,
                )
                logging.info(
                    "Timeline page %s: kept %s, skipped %s.",
                    page_number,
                    kept,
                    skipped,
                )
                if kept == 0 and skipped == 0:
                    logging.info("Timeline page had no new headlines. Stopping.")
                    break
        finally:
            browser.close()

    return pd.DataFrame(rows, columns=COLUMNS)


def main() -> None:
    args = parse_args()
    keywords = parse_keywords(args.keywords)
    if keywords:
        logging.info("Keeping headlines that match: %s", ", ".join(keywords))

    frame = scrape_cafef(args.max_articles, keywords, args.headed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "cafef_news.csv"
    xlsx_path = OUTPUT_DIR / "cafef_news.xlsx"
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    frame.to_excel(xlsx_path, index=False)
    logging.info("Saved %s headlines to %s and %s", len(frame), csv_path, xlsx_path)
    if frame.empty:
        logging.warning("No headlines were saved.")
        return
    print(frame.to_string(index=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    main()
