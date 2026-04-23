#!/usr/bin/env python3
"""
Scrape New Central MRT + Config API reference pages.
Parallel with ThreadPoolExecutor for speed.
"""
import html as htmllib
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import urllib.request

OUTPUT_DIR = Path(__file__).parent / "markdown"
OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

def load_urls():
    mrt = json.loads(Path("/tmp/mrt_urls.json").read_text())
    cfg = json.loads(Path("/tmp/cfg_urls.json").read_text())
    return mrt + cfg

def slug_from_url(url):
    return re.sub(r"[^a-z0-9_-]", "_", url.split("//")[1].replace("/", "_").lower())

def fetch_html(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")

def extract_content(html):
    m = re.search(r'dehydrated="(.*?)"(?=\s*>)', html, re.DOTALL)
    if m:
        inner = htmllib.unescape(m.group(1))
        title_m = re.search(r'<article[^>]*>.*?<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        title_html = f"<h1>{title_m.group(1)}</h1>\n" if title_m else ""
        return title_html + inner
    m2 = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if m2:
        return m2.group(1)
    return html

def html_to_markdown(html_content, url):
    result = subprocess.run(
        ["pandoc", "-f", "html", "-t", "markdown_strict", "--wrap=none"],
        input=html_content.encode(),
        capture_output=True,
        timeout=30,
    )
    md = result.stdout.decode("utf-8", errors="replace")
    return f"<!-- source: {url} -->\n\n" + md

def scrape_page(url):
    slug = slug_from_url(url)
    out_path = OUTPUT_DIR / f"{slug}.md"
    if out_path.exists():
        return f"SKIP {slug}"
    try:
        html = fetch_html(url)
        content = extract_content(html)
        md = html_to_markdown(content, url)
        out_path.write_text(md, encoding="utf-8")
        return f"OK {slug} ({len(md)})"
    except Exception as e:
        return f"ERROR {url}: {e}"

def main():
    urls = load_urls()
    print(f"Scraping {len(urls)} reference pages -> {OUTPUT_DIR}")
    done = 0
    errors = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(scrape_page, url): url for url in urls}
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            if result.startswith("ERROR"):
                errors.append(result)
            if done % 50 == 0 or done == len(urls):
                print(f"  [{done}/{len(urls)}] {result}")
    print(f"\nDone. {len(errors)} errors.")
    for e in errors:
        print(" ", e)

if __name__ == "__main__":
    main()
