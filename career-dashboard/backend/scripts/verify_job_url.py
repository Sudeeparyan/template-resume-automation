#!/usr/bin/env python3
"""
Verify job listing URLs for the portable career workspace.

Checks whether job listing URLs are likely active, expired, broken, or need manual review.
Handles LinkedIn auth walls, career portal detection, redirects, and JS-rendered pages.

Usage:
    Single URL:  python3 verify_job_url.py --url "https://..."
    Batch mode:  python3 verify_job_url.py --file data/pipeline.md
    With delay:  python3 verify_job_url.py --file data/pipeline.md --delay 8
"""

import argparse
import html
import json
import re
import socket
import sys
import time
from html.parser import HTMLParser
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ─── Patterns that indicate a job is no longer accepting applications ─────────
EXPIRED_PATTERNS = [
    # Generic expired indicators
    r"no\s+longer\s+accepting\s+applications",
    r"this\s+job\s+is\s+no\s+longer\s+available",
    r"this\s+position\s+has\s+been\s+filled",
    r"job\s+expired",
    r"applications?\s+closed",
    r"this\s+job\s+has\s+expired",
    r"this\s+job\s+posting\s+is\s+no\s+longer\s+available",
    r"the\s+job\s+you\s+are\s+looking\s+for\s+is\s+no\s+longer",
    r"this\s+listing\s+has\s+expired",
    r"this\s+role\s+has\s+been\s+filled",
    r"position\s+filled",
    r"no\s+longer\s+available",
    # Page not found / 404
    r"we\s+can['']t\s+find\s+this\s+page",
    r"it\s+looks\s+like\s+this\s+page\s+doesn['']t\s+exist",
    r"page\s+not\s+found",
    r"404\s+not\s+found",
    r"oops!\s+we\s+couldn['']t\s+find",
    r"sorry,?\s+this\s+page\s+(doesn['']t|does\s+not)\s+exist",
    # Archived / Closed
    r"this\s+job\s+has\s+been\s+archived",
    r"sorry,?\s+this\s+listing\s+is\s+no\s+longer\s+available",
    r"this\s+opportunity\s+is\s+closed",
    r"this\s+requisition\s+is\s+no\s+longer",
    r"this\s+position\s+is\s+no\s+longer\s+(open|available|active)",
    r"this\s+job\s+ad\s+is\s+no\s+longer\s+active",
    r"job\s+listing\s+not\s+found",
    r"this\s+job\s+is\s+closed",
    r"this\s+vacancy\s+has\s+(been\s+)?closed",
    r"position\s+is\s+closed",
    r"role\s+is\s+no\s+longer\s+(open|available)",
    # Deadline passed
    r"apply\s+by\s+date.*has\s+passed",
    r"application\s+deadline\s+has\s+passed",
    r"deadline.*passed",
    r"the\s+deadline\s+for\s+this\s+(job|position|role)\s+has\s+passed",
    # Workable / Ashby / Greenhouse specific
    r"this\s+position\s+is\s+no\s+longer\s+listed",
    r"this\s+job\s+is\s+not\s+available",
    r"job\s+not\s+found",
]

# ─── Patterns for career portal homepages (not specific job listings) ─────────
CAREER_PORTAL_PATTERNS = [
    r"/careers/?$",
    r"/careers/jobs/?$",
    r"/company/careers/?$",
    r"/en/corporate/careers/?$",
    r"/jobs/?$",
    r"myworkdayjobs\.com/.*External(_Career_Site)?/?$",
    r"recruitee\.com/?$",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class VisibleTextExtractor(HTMLParser):
    """Collect visible page text without third-party HTML dependencies."""

    hidden_tags = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.hidden_tags:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.hidden_tags and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self.parts)


def html_to_text(source: str) -> str:
    """Convert HTML to normalized visible text."""
    parser = VisibleTextExtractor()
    try:
        parser.feed(source)
        parser.close()
        text = parser.text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", source)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def classify_url(url: str) -> str:
    """Classify URL type: 'linkedin', 'career_portal', or 'direct_job'."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    if "linkedin.com" in domain:
        return "linkedin"

    for pattern in CAREER_PORTAL_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return "career_portal"

    # Check for known career page domains without specific job IDs
    if any(portal in url.lower() for portal in [
        "workable.com/", "ashbyhq.com/", "recruitee.com/"
    ]):
        # These are job board portals - check if they point to a specific job or listing page
        path = parsed.path.rstrip("/")
        # If the path has only one segment (e.g., /companyname), it's a portal
        segments = [s for s in path.split("/") if s]
        if len(segments) <= 1:
            return "career_portal"

    return "direct_job"


def _first_url(text: str) -> Optional[str]:
    """Return the first Markdown-link or bare HTTP URL in text."""
    markdown_link = re.search(r"\[[^\]]+\]\((https?://[^)\s]+)\)", text)
    if markdown_link:
        return markdown_link.group(1)

    bare_url = re.search(r"https?://[^\s|)>]+", text)
    if bare_url:
        return bare_url.group(0).rstrip(".,;")
    return None


def _clean_cell(value: str) -> str:
    """Remove common Markdown decoration from a table/pipeline cell."""
    value = re.sub(r"\[[^\]]+\]\((https?://[^)]+)\)", r"\1", value)
    value = re.sub(r"[*_`]", "", value)
    return value.strip()


def extract_urls_from_markdown(filepath: str) -> list[dict]:
    """Extract job URLs and nearby metadata from supported Markdown formats."""
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.split("\n")
    current_section = "Unknown"
    seen_urls = set()

    for line_number, line in enumerate(lines, start=1):
        heading = re.match(r"^#{1,6}\s+(.+)", line)
        if heading:
            current_section = heading.group(1).strip()

        url = _first_url(line)
        if not url or url in seen_urls:
            continue

        company = "Unknown"
        role = "Unknown"
        job_type = "Unknown"
        location = "Unknown"
        track = "Unknown"
        rank = len(entries) + 1

        # Current pipeline format:
        # - [ ] URL | Company | Role | Track | Location
        if re.match(r"^\s*-\s*\[[ xX]\]", line):
            cells = [_clean_cell(cell) for cell in line.split("|")]
            if len(cells) >= 3:
                company = cells[1] or company
                role = cells[2] or role
            if len(cells) >= 4:
                track = cells[3] or track
            if len(cells) >= 5:
                location = cells[4] or location

        # Legacy/standard table rows:
        # | # | Company | Role | Type | Location | [Apply](URL) |
        elif line.lstrip().startswith("|"):
            cells = [_clean_cell(cell) for cell in line.strip().strip("|").split("|")]
            if cells and cells[0].isdigit():
                rank = int(cells[0])
                cells = cells[1:]
            if len(cells) >= 2:
                company = cells[0] or company
                role = cells[1] or role
            if len(cells) >= 3:
                job_type = cells[2] or job_type
            if len(cells) >= 4:
                location = cells[3] or location

        entries.append({
            "rank": rank,
            "company": company,
            "role": role,
            "type": job_type,
            "location": location,
            "track": track,
            "url": url,
            "url_type": classify_url(url),
            "section": current_section,
            "source_line": line_number,
        })
        seen_urls.add(url)

    return entries


def check_url(url: str, url_type: str = "direct_job") -> dict:
    """Check a single URL for expiration indicators."""
    result = {
        "url": url,
        "status": "LIKELY_ACTIVE",
        "reason": "",
        "url_type": url_type,
        "http_status": None,
        "final_url": None,
        "redirect_detected": False,
    }

    try:
        request = Request(url, headers=HEADERS)
        with urlopen(request, timeout=20) as response:
            status = getattr(response, "status", response.getcode())
            final_url = response.geturl()
            payload = response.read(5_000_000)
            charset = response.headers.get_content_charset() or "utf-8"

        result["http_status"] = status
        result["final_url"] = final_url

        # Detect significant redirects (e.g., job URL -> careers homepage)
        if final_url != url:
            original_path = urlparse(url).path.rstrip("/")
            final_path = urlparse(final_url).path.rstrip("/")
            if original_path != final_path:
                result["redirect_detected"] = True
                # If a specific job URL redirects to a generic careers page, flag it
                if classify_url(final_url) == "career_portal" and url_type != "career_portal":
                    result["status"] = "EXPIRED"
                    result["reason"] = f"Redirected to careers page: {final_url}"
                    return result

        # Check HTTP errors
        if status >= 400:
            result["status"] = "BROKEN"
            result["reason"] = f"HTTP {status}"
            return result

        # For career portals, mark as needing manual browser check
        if url_type == "career_portal":
            result["status"] = "NEEDS_MANUAL_CHECK"
            result["reason"] = "Career portal homepage - need to search for specific role"
            return result

        # Parse HTML and check for expired patterns
        source = payload.decode(charset, errors="replace")
        page_text = html_to_text(source).lower()

        for pattern in EXPIRED_PATTERNS:
            match = re.search(pattern, page_text)
            if match:
                result["status"] = "EXPIRED"
                result["reason"] = match.group(0).strip()
                return result

        # LinkedIn-specific: check if the page has minimal content (auth wall)
        if url_type == "linkedin":
            # LinkedIn shows limited content to non-authenticated users
            # If page is very short or lacks job details, it might be behind auth wall
            if len(page_text) < 200:
                result["status"] = "NEEDS_MANUAL_CHECK"
                result["reason"] = "LinkedIn page with minimal content - may be behind auth wall"
                return result

    except HTTPError as exc:
        result["http_status"] = exc.code
        result["final_url"] = exc.geturl()
        if exc.code in {401, 403, 429}:
            result["status"] = "NEEDS_MANUAL_CHECK"
            result["reason"] = f"HTTP {exc.code} requires browser/manual verification"
        else:
            result["status"] = "BROKEN"
            result["reason"] = f"HTTP {exc.code}"
    except (socket.timeout, TimeoutError):
        result["status"] = "BROKEN"
        result["reason"] = "Connection timed out (20s)"
    except URLError as exc:
        result["status"] = "BROKEN"
        result["reason"] = f"Connection error: {str(exc.reason)[:100]}"
    except OSError as exc:
        result["status"] = "BROKEN"
        result["reason"] = str(exc)[:150]

    return result


def main():
    parser = argparse.ArgumentParser(description="Verify job listing URLs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Single URL to check")
    group.add_argument("--file", help="Markdown file with job URLs to check in batch")
    parser.add_argument("--delay", type=float, default=8.0,
                        help="Delay between requests in seconds (default: 8.0)")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional JSON output file; results always print to stdout")
    args = parser.parse_args()

    results = []

    if args.url:
        url_type = classify_url(args.url)
        result = check_url(args.url, url_type)
        result["company"] = "Unknown"
        result["role"] = "Unknown"
        results.append(result)
    else:
        entries = extract_urls_from_markdown(args.file)
        if not entries:
            print(json.dumps({"error": "No URLs found in file"}))
            sys.exit(1)

        # Categorize entries
        linkedin_count = sum(1 for e in entries if e["url_type"] == "linkedin")
        portal_count = sum(1 for e in entries if e["url_type"] == "career_portal")
        direct_count = sum(1 for e in entries if e["url_type"] == "direct_job")

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  JOB URL VERIFIER — {len(entries)} URLs to check", file=sys.stderr)
        print(f"  LinkedIn: {linkedin_count} | Career Portals: {portal_count} | Direct: {direct_count}", file=sys.stderr)
        print(f"  Delay: {args.delay}s between requests", file=sys.stderr)
        print(f"  Estimated time: ~{len(entries) * args.delay / 60:.1f} minutes", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        for i, entry in enumerate(entries):
            print(
                f"[{i+1}/{len(entries)}] #{entry['rank']} {entry['company']} — {entry['role']}",
                file=sys.stderr
            )
            print(f"  URL type: {entry['url_type']} | {entry['url']}", file=sys.stderr)

            result = check_url(entry["url"], entry["url_type"])
            result["company"] = entry["company"]
            result["role"] = entry["role"]
            result["type"] = entry["type"]
            result["location"] = entry["location"]
            result["rank"] = entry["rank"]
            result["track"] = entry["track"]
            result["section"] = entry["section"]
            result["source_line"] = entry["source_line"]
            results.append(result)

            # Status icons
            icons = {
                "LIKELY_ACTIVE": "✅",
                "EXPIRED": "❌",
                "BROKEN": "⚠️",
                "NEEDS_MANUAL_CHECK": "🔍",
            }
            icon = icons.get(result["status"], "❓")
            print(f"  {icon} {result['status']}: {result.get('reason', 'Looks active')}", file=sys.stderr)

            if result.get("redirect_detected"):
                print(f"  ↪ Redirected to: {result['final_url']}", file=sys.stderr)

            print(file=sys.stderr)

            # Rate limiting
            if i < len(entries) - 1:
                time.sleep(args.delay)

    # Output JSON results
    output_file = args.output
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

    # Summary
    expired = [r for r in results if r["status"] == "EXPIRED"]
    broken = [r for r in results if r["status"] == "BROKEN"]
    active = [r for r in results if r["status"] == "LIKELY_ACTIVE"]
    needs_check = [r for r in results if r["status"] == "NEEDS_MANUAL_CHECK"]

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  VERIFICATION COMPLETE", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  ✅ Likely active:       {len(active)}", file=sys.stderr)
    print(f"  ❌ Expired:             {len(expired)}", file=sys.stderr)
    print(f"  ⚠️  Broken:              {len(broken)}", file=sys.stderr)
    print(f"  🔍 Needs Browser Check: {len(needs_check)}", file=sys.stderr)
    if output_file:
        print(f"  Results saved to: {output_file}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    if expired:
        print(f"❌ EXPIRED LISTINGS:", file=sys.stderr)
        for r in expired:
            print(f"  - #{r.get('rank', '?')} {r['company']} | {r['role']}", file=sys.stderr)
            print(f"    Reason: {r['reason']}", file=sys.stderr)

    if broken:
        print(f"\n⚠️  BROKEN LINKS:", file=sys.stderr)
        for r in broken:
            print(f"  - #{r.get('rank', '?')} {r['company']} | {r['role']}", file=sys.stderr)
            print(f"    Reason: {r['reason']}", file=sys.stderr)

    if needs_check:
        print(f"\n🔍 NEEDS BROWSER VERIFICATION:", file=sys.stderr)
        for r in needs_check:
            print(f"  - #{r.get('rank', '?')} {r['company']} | {r['role']}", file=sys.stderr)
            print(f"    Reason: {r['reason']}", file=sys.stderr)

    # Print JSON to stdout for piping
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
