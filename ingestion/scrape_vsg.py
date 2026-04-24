#!/usr/bin/env python3
"""Scrape VSG pages using Playwright (real Chrome, headless=False to bypass Akamai)."""
import json
import random
import re
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).parent / "sources" / "vsg_docs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def slug_from_url(url):
    path = url.split("/techdocs/VSG/docs/")[-1].strip("/")
    return re.sub(r"[^a-z0-9_-]", "_", path.lower()).strip("_")

def extract_content(html):
    for pattern in [
        r'<main[^>]*>(.*?)</main>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]+\brole=["\']main["\'][^>]*>(.*?)</div>',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1)
    body = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    return body.group(1) if body else html

def html_to_markdown(content, url):
    result = subprocess.run(
        ["pandoc", "-f", "html", "-t", "markdown_strict", "--wrap=none"],
        input=content.encode(), capture_output=True, timeout=30,
    )
    md = result.stdout.decode("utf-8", errors="replace")
    return f"<!-- source: {url} -->\n\n" + md

def main():
    with open("/tmp/vsg_urls.json") as f:
        urls = json.load(f)
    print(f"Scraping {len(urls)} VSG pages -> {OUTPUT_DIR}")
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        for i, url in enumerate(urls, 1):
            slug = slug_from_url(url)
            out_path = OUTPUT_DIR / f"{slug}.md"
            if out_path.exists():
                print(f"  [{i}/{len(urls)}] SKIP {slug}")
                continue
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if "Access Denied" in page.title():
                    raise Exception("403 Access Denied")
                html = page.content()
                content = extract_content(html)
                md = html_to_markdown(content, url)
                out_path.write_text(md, encoding="utf-8")
                print(f"  [{i}/{len(urls)}] OK {slug} ({len(md)})")
            except Exception as e:
                errors.append(url)
                print(f"  [{i}/{len(urls)}] ERROR {url}: {e}")
            time.sleep(random.uniform(4.0, 7.0))
        browser.close()
    print(f"\nDone. {len(errors)} errors.")
    with open("/tmp/vsg_missing.json", "w") as f:
        json.dump(errors, f, indent=2)
    for e in errors[:20]:
        print(" ", e)

if __name__ == "__main__":
    main()
