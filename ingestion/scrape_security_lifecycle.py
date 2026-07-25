#!/usr/bin/env python3
"""Refresh official security-advisory and product-lifecycle RAG sources.

Sources:
- HPE Aruba Networking CSAF advisory archive (all published products).
- HPE Networking end-of-sale XML archive (all networking categories).
- Juniper Mist and Apstra lifecycle pages used by centralmcp's optional
  Juniper product backends.

Generated files live under ingestion/sources/ and are intentionally git-ignored.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
SOURCES_DIR = ROOT / "sources"
SECURITY_DIR = SOURCES_DIR / "security_advisories"
LIFECYCLE_DIR = SOURCES_DIR / "lifecycle_notices"
JUNIPER_LIFECYCLE_DIR = SOURCES_DIR / "juniper_lifecycle"
JUNIPER_SECURITY_DIR = SOURCES_DIR / "juniper_security_advisories"

ARUBA_CSAF_BASE = "https://csaf.arubanetworking.hpe.com/"
ARUBA_CSAF_CHANGES = urljoin(ARUBA_CSAF_BASE, "changes.csv")
HPE_LIFECYCLE_BASE = (
    "https://support.hpe.com/docs/display/public/hpe-networking-eos/"
)
HPE_LIFECYCLE_XML = urljoin(HPE_LIFECYCLE_BASE, "data/xml/eos/eos.xml")
HPE_LIFECYCLE_POLICY = urljoin(HPE_LIFECYCLE_BASE, "information.html")
ARUBA_HARDWARE_EOS_PDF = (
    "https://asp-documents.arubanetworks.com/portals/0/el/"
    "Aruba-Hardware-End-of-Sale-List.pdf"
)

JUNIPER_LIFECYCLE_URLS = {
    "mist-access-points-and-mist-edge": (
        "https://support.juniper.net/support/eol/product/juniper_ap_series/"
    ),
    "apstra-hardware": "https://support.juniper.net/support/eol/product/apstra/",
    "apstra-software": "https://support.juniper.net/support/eol/software/apstra/",
}
JUNIPER_SITEMAPS = (
    "https://supportportal.juniper.net/s/sitemap-topicarticle-1.xml",
    "https://supportportal.juniper.net/s/sitemap-topicarticle-weekly.xml",
)

HEADERS = {
    "User-Agent": "centralmcp-rag-ingestion/1.0 (+https://github.com/secure-ssid/centralmcp)",
    "Accept": "application/json,text/plain,text/html,application/xml;q=0.9,*/*;q=0.8",
}

HPE_CATEGORIES = {
    "1": "Switches",
    "2": "Wireless",
    "3": "Routers",
    "4": "Network Management",
    "5": "Network Security",
    "6": "Unified Communications",
    "7": "Accessories",
    "8": "SaaS",
}

TABLE_FIELDS = {
    "TableA": "Product SKU",
    "TableB": "Product Description",
    "TableC": "Replacement Product SKU",
    "TableD": "Replacement Product Description",
    "TableE": "Custom Field 1",
    "TableF": "Custom Field 2",
    "TableG": "Custom Field 3",
    "TableH": "Custom Field 4",
    "TableI": "Custom Field 5",
    "TableJ": "Custom Field 6",
}


class SourceFetchError(RuntimeError):
    """An official source could not be fetched or parsed."""


def fetch_bytes(url: str, *, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceFetchError(f"failed to fetch {url}: {exc}") from exc


def fetch_text(url: str, *, timeout: float = 60.0) -> str:
    return fetch_bytes(url, timeout=timeout).decode("utf-8", errors="replace")


def _safe_advisory_path(relative_path: str) -> PurePosixPath:
    path = PurePosixPath(relative_path.strip())
    if (
        not relative_path.strip()
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".json"
    ):
        raise SourceFetchError(f"unsafe advisory path in CSAF index: {relative_path!r}")
    return path


def parse_changes_csv(text: str) -> list[tuple[PurePosixPath, str]]:
    records: dict[str, tuple[PurePosixPath, str]] = {}
    for row in csv.reader(StringIO(text)):
        if not row:
            continue
        if len(row) != 2:
            raise SourceFetchError(f"invalid CSAF changes.csv row: {row!r}")
        path = _safe_advisory_path(row[0])
        changed_at = row[1].strip()
        key = path.as_posix().lower()
        existing = records.get(key)
        if existing is None or changed_at > existing[1]:
            records[key] = (path, changed_at)
    return [records[key] for key in sorted(records)]


def _walk_product_names(branches: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(branches, list):
        return names
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        name = str(branch.get("name") or "").strip()
        if name:
            names.append(name)
        names.extend(_walk_product_names(branch.get("branches")))
    return names


def render_csaf_advisory(spec: dict[str, Any], source_url: str) -> str:
    document = spec.get("document") if isinstance(spec.get("document"), dict) else {}
    tracking = (
        document.get("tracking") if isinstance(document.get("tracking"), dict) else {}
    )
    severity = (
        document.get("aggregate_severity")
        if isinstance(document.get("aggregate_severity"), dict)
        else {}
    )
    advisory_id = str(tracking.get("id") or "unknown")
    title = str(document.get("title") or advisory_id)
    lines = [
        f"<!-- source: {source_url} -->",
        "",
        f"# {title}",
        "",
        f"- Advisory ID: {advisory_id}",
        f"- Aggregate severity: {severity.get('text', 'Not stated')}",
        f"- Initial release: {tracking.get('initial_release_date', 'Not stated')}",
        f"- Current release: {tracking.get('current_release_date', 'Not stated')}",
        f"- Status: {tracking.get('status', 'Not stated')}",
        f"- Revision: {tracking.get('version', 'Not stated')}",
        "",
    ]

    product_tree = spec.get("product_tree")
    product_names = _walk_product_names(
        product_tree.get("branches") if isinstance(product_tree, dict) else None
    )
    if product_names:
        lines.extend(["## Product catalog", "", *[f"- {name}" for name in product_names], ""])

    notes = document.get("notes")
    if isinstance(notes, list):
        for note in notes:
            if not isinstance(note, dict):
                continue
            heading = str(note.get("title") or note.get("category") or "Advisory note")
            text = str(note.get("text") or "").strip()
            if text:
                lines.extend([f"## {heading}", "", text, ""])

    vulnerabilities = spec.get("vulnerabilities")
    if isinstance(vulnerabilities, list) and vulnerabilities:
        lines.extend(["## Vulnerabilities", ""])
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            cve = str(vulnerability.get("cve") or vulnerability.get("title") or "Unassigned")
            lines.append(f"### {cve}")
            if vulnerability.get("title") and vulnerability.get("title") != cve:
                lines.append(str(vulnerability["title"]))
            scores = vulnerability.get("scores")
            if isinstance(scores, list):
                for score in scores:
                    if not isinstance(score, dict):
                        continue
                    cvss = score.get("cvss_v3") or score.get("cvss_v2")
                    if isinstance(cvss, dict):
                        lines.append(
                            "- CVSS: "
                            f"{cvss.get('baseScore', 'unknown')} "
                            f"({cvss.get('baseSeverity', 'severity not stated')}); "
                            f"vector {cvss.get('vectorString', 'not stated')}"
                        )
            for note in vulnerability.get("notes") or []:
                if isinstance(note, dict) and note.get("text"):
                    lines.append(str(note["text"]).strip())
            for remediation in vulnerability.get("remediations") or []:
                if isinstance(remediation, dict) and remediation.get("details"):
                    category = remediation.get("category", "remediation")
                    lines.extend(
                        [
                            f"#### {str(category).replace('_', ' ').title()}",
                            str(remediation["details"]).strip(),
                        ]
                    )
            lines.append("")

    references = document.get("references")
    if isinstance(references, list) and references:
        lines.extend(["## References", ""])
        for reference in references:
            if not isinstance(reference, dict) or not reference.get("url"):
                continue
            lines.append(
                f"- {reference.get('summary', 'Reference')}: {reference['url']}"
            )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _load_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def sync_aruba_security(
    *, refresh_existing: bool = False, workers: int = 4
) -> tuple[int, int]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = SECURITY_DIR / "_manifest.json"
    old_manifest = _load_manifest(manifest_path)
    records = parse_changes_csv(fetch_text(ARUBA_CSAF_CHANGES))
    new_manifest: dict[str, str] = {}
    skipped = 0
    pending: list[tuple[PurePosixPath, Path, str]] = []

    for relative_path, changed_at in records:
        key = relative_path.as_posix()
        output = SECURITY_DIR / Path(
            *PurePosixPath(key.lower()).with_suffix(".md").parts
        )
        new_manifest[key] = changed_at
        if (
            not refresh_existing
            and output.exists()
            and old_manifest.get(key) == changed_at
        ):
            skipped += 1
            continue
        pending.append((relative_path, output, urljoin(ARUBA_CSAF_BASE, key)))

    def fetch_one(item: tuple[PurePosixPath, Path, str]) -> None:
        _relative_path, output, source_url = item
        try:
            spec = json.loads(fetch_text(source_url))
        except json.JSONDecodeError as exc:
            raise SourceFetchError(f"invalid CSAF JSON from {source_url}: {exc}") from exc
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_csaf_advisory(spec, source_url), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(fetch_one, pending))

    manifest_path.write_text(
        json.dumps(new_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return len(pending), skipped


def _child_text(item: ET.Element, name: str) -> str:
    child = item.find(name)
    return (child.text or "").strip() if child is not None else ""


def parse_hpe_lifecycle_xml(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceFetchError(f"invalid HPE lifecycle XML: {exc}") from exc

    announcements: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in root.findall("Item"):
        item_id = _child_text(item, "ID")
        if item_id:
            if current is not None:
                announcements.append(current)
            current = {
                "id": item_id,
                "type": _child_text(item, "Type"),
                "name": _child_text(item, "Name"),
                "published": _child_text(item, "PubDate"),
                "description": _child_text(item, "Desc"),
                "notes": _child_text(item, "Notes"),
                "announcement": _child_text(item, "LinkA"),
                "product_url": _child_text(item, "LinkB"),
                "rows": [],
            }
            continue
        if current is None:
            continue
        row = {
            label: _child_text(item, field)
            for field, label in TABLE_FIELDS.items()
            if _child_text(item, field)
        }
        if row:
            current["rows"].append(row)
    if current is not None:
        announcements.append(current)

    return [
        item
        for item in announcements
        if str(item["id"]).isdigit() and item.get("name")
    ]


def render_hpe_lifecycle_notice(notice: dict[str, Any]) -> str:
    source_url = HPE_LIFECYCLE_XML
    lines = [
        f"<!-- source: {source_url} -->",
        "",
        f"# {notice['name']}",
        "",
        f"- Notice ID: {notice['id']}",
        f"- Product category: {HPE_CATEGORIES.get(str(notice.get('type')), 'Unknown')}",
        f"- Published: {notice.get('published') or 'Not stated'}",
    ]
    if notice.get("announcement"):
        lines.append(
            f"- Official announcement: {urljoin(HPE_LIFECYCLE_BASE, notice['announcement'])}"
        )
    if notice.get("product_url"):
        lines.append(f"- Product page: {notice['product_url']}")
    lines.append("")
    if notice.get("description"):
        lines.extend(["## Lifecycle announcement", "", notice["description"], ""])
    if notice.get("notes"):
        lines.extend(["## Notes", "", notice["notes"], ""])
    rows = notice.get("rows") or []
    if rows:
        lines.extend(["## Affected and replacement products", ""])
        for row in rows:
            lines.append(
                "- " + "; ".join(f"{key}: {value}" for key, value in row.items())
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:100] or "notice"


def sync_hpe_lifecycle() -> int:
    LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True)
    notices = parse_hpe_lifecycle_xml(fetch_text(HPE_LIFECYCLE_XML))
    for notice in notices:
        output = LIFECYCLE_DIR / f"{notice['id']}-{_slug(notice['name'])}.md"
        output.write_text(render_hpe_lifecycle_notice(notice), encoding="utf-8")

    policy_html = fetch_text(HPE_LIFECYCLE_POLICY)
    policy_text = BeautifulSoup(policy_html, "html.parser").get_text("\n", strip=True)
    (LIFECYCLE_DIR / "hpe-networking-lifecycle-policy.md").write_text(
        f"<!-- source: {HPE_LIFECYCLE_POLICY} -->\n\n"
        "# HPE Networking product lifecycle policy\n\n"
        f"{policy_text}\n",
        encoding="utf-8",
    )
    from pypdf import PdfReader

    hardware_pdf = PdfReader(BytesIO(fetch_bytes(ARUBA_HARDWARE_EOS_PDF)))
    hardware_text = "\n\n".join(
        page.extract_text() or "" for page in hardware_pdf.pages
    ).strip()
    if not hardware_text:
        raise SourceFetchError(
            "official Aruba hardware End of Sale PDF contained no extractable text"
        )
    (LIFECYCLE_DIR / "aruba-hardware-end-of-sale-list.md").write_text(
        f"<!-- source: {ARUBA_HARDWARE_EOS_PDF} -->\n\n"
        "# HPE Aruba hardware End of Sale list\n\n"
        f"{hardware_text}\n",
        encoding="utf-8",
    )
    return len(notices)


_JUNIPER_CONTENT_RE = re.compile(r'"(?:description|htmlContent)":\'(.*?)\'\s*[,}]', re.DOTALL)


def render_juniper_lifecycle_page(page_html: str, source_url: str) -> str:
    sections: list[str] = []
    for encoded in _JUNIPER_CONTENT_RE.findall(page_html):
        decoded = encoded.replace("\\'", "'").replace("\\/", "/")
        text = BeautifulSoup(html.unescape(decoded), "html.parser").get_text(
            "\n", strip=True
        )
        if text and text not in sections:
            sections.append(text)
    if not sections:
        raise SourceFetchError(
            f"no lifecycle description/table content found in Juniper page {source_url}"
        )
    title_soup = BeautifulSoup(page_html, "html.parser")
    title = title_soup.title.get_text(strip=True) if title_soup.title else source_url
    return (
        f"<!-- source: {source_url} -->\n\n# {title}\n\n"
        + "\n\n".join(sections)
        + "\n"
    )


def sync_juniper_lifecycle() -> int:
    JUNIPER_LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True)
    for slug, url in JUNIPER_LIFECYCLE_URLS.items():
        output = JUNIPER_LIFECYCLE_DIR / f"{slug}.md"
        output.write_text(
            render_juniper_lifecycle_page(fetch_text(url), url), encoding="utf-8"
        )
    return len(JUNIPER_LIFECYCLE_URLS)


def parse_juniper_security_sitemap(xml_text: str) -> set[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceFetchError(f"invalid Juniper sitemap XML: {exc}") from exc
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls: set[str] = set()
    for element in root.findall(".//sm:loc", namespace):
        url = (element.text or "").strip()
        lowered = url.lower()
        if (
            "/s/article/" in lowered
            and "security-bulletin" in lowered
            and ("apstra" in lowered or "mist" in lowered)
        ):
            urls.add(url)
    return urls


def discover_juniper_security_urls() -> list[str]:
    urls: set[str] = set()
    for sitemap in JUNIPER_SITEMAPS:
        urls.update(parse_juniper_security_sitemap(fetch_text(sitemap)))
    return sorted(urls)


def render_juniper_security_advisory(title: str, body_text: str, source_url: str) -> str:
    text = body_text.strip()
    if not text or "Product Affected" not in text:
        raise SourceFetchError(
            f"rendered Juniper advisory did not contain advisory content: {source_url}"
        )
    return f"<!-- source: {source_url} -->\n\n# {title.strip()}\n\n{text}\n"


def sync_juniper_security() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SourceFetchError(
            "Playwright is required for Juniper security advisories; install "
            "project dependencies and run `playwright install chromium`."
        ) from exc

    urls = discover_juniper_security_urls()
    JUNIPER_SECURITY_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        for url in urls:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.get_by_text("Product Affected", exact=True).wait_for(
                    timeout=30_000
                )
            except Exception as exc:
                raise SourceFetchError(
                    f"Juniper advisory did not render in time: {url}: {exc}"
                ) from exc
            title = page.title()
            body = page.locator("body").inner_text()
            slug = _slug(url.rsplit("/", 1)[-1])
            (JUNIPER_SECURITY_DIR / f"{slug}.md").write_text(
                render_juniper_security_advisory(title, body, url),
                encoding="utf-8",
            )
        browser.close()
    return len(urls)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        choices=(
            "security",
            "hpe-lifecycle",
            "juniper-lifecycle",
            "juniper-security",
        ),
        help="Refresh only one source family; default refreshes all.",
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Re-download unchanged Aruba CSAF advisories instead of using changes.csv.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent Aruba CSAF downloads (default: 4).",
    )
    args = parser.parse_args()

    if args.only in (None, "security"):
        fetched, skipped = sync_aruba_security(
            refresh_existing=args.refresh_existing,
            workers=args.workers,
        )
        print(f"Aruba security advisories: {fetched} fetched, {skipped} unchanged")
    if args.only in (None, "hpe-lifecycle"):
        print(f"HPE lifecycle notices: {sync_hpe_lifecycle()} rendered")
    if args.only in (None, "juniper-lifecycle"):
        print(f"Juniper lifecycle pages: {sync_juniper_lifecycle()} rendered")
    if args.only in (None, "juniper-security"):
        print(f"Juniper security advisories: {sync_juniper_security()} rendered")


if __name__ == "__main__":
    main()
