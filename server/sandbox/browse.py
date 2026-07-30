#!/usr/bin/env python3
"""Render a page with a headless browser and print its visible text.

For JS-heavy sites (Behance, Dribbble, Upwork, most modern SPAs) plain curl gets
an empty shell; this loads the page in Chromium, lets it render, and prints what
a human would actually see. Usage: browse.py <url>
"""
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: browse.py <url>", file=sys.stderr)
        return 2
    url = sys.argv[1].strip()
    with sync_playwright() as p:
        # --no-sandbox: the container already IS the sandbox, and Chromium's own
        # sandbox can't run as root inside it.
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (Kith)")
            page.goto(url, wait_until="networkidle", timeout=30000)
            text = page.inner_text("body")
        finally:
            browser.close()
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
