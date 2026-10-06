"""Verify current public quota clauses before issuing a limits-review timestamp.

No provider credentials or report input. Source changes fail closed for review.
This audit does not establish account plans, consumption or billing prevention.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

try:
    from .probe_providers import MAX_BYTES, NoRedirect, ProbeError
except ImportError:
    from probe_providers import MAX_BYTES, NoRedirect, ProbeError

SOURCES = {
    "pages": "https://developers.cloudflare.com/pages/platform/limits/",
    "workers": "https://developers.cloudflare.com/workers/platform/limits/",
    "firestore": "https://firebase.google.com/docs/firestore/quotas",
    "github": "https://docs.github.com/en/billing/concepts/product-billing/github-actions",
}
LIMITS = {
    "pages_builds": 500,
    "workers_requests": 100_000,
    "firestore_reads": 50_000,
    "firestore_writes": 20_000,
    "firestore_deletes": 20_000,
    "firestore_storage_bytes": 1024**3,
    "firestore_outbound_bytes": 10 * 1024**3,
    "actions_cache_storage_bytes": 10 * 1024**3,
}


def normalize(value):
    return " ".join(value.split())


class Document(HTMLParser):
    VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.tables = []
        self.blocks = []
        self.block_stack = []
        self.table = self.row = self.cell = self.heading = None
        self.section = ()
        self.headings = {}
        self.main_count = 0
        self.feed(raw.decode("utf-8"))
        self.close()
        if self.main_count != 1 or self.stack or self.table is not None:
            raise ProbeError("invalid_document_structure")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent_hidden = self.stack[-1][1] if self.stack else False
        in_main = self.stack[-1][2] if self.stack else False
        hidden = (
            parent_hidden
            or tag in {"script", "style", "template", "noscript", "del", "s", "nav", "footer"}
            or "hidden" in attrs
            or "inert" in attrs
            or attrs.get("aria-hidden") == "true"
            or re.search(
                r"display\s*:\s*none|visibility\s*:\s*hidden", attrs.get("style", ""), re.I
            )
            or set(attrs.get("class", "").split())
            & {"hidden", "sr-only", "visually-hidden", "d-none"}
        )
        if tag == "main" and not hidden:
            self.main_count += 1
            in_main = True
        if tag not in self.VOID:
            self.stack.append((tag, bool(hidden), in_main))
        if hidden or not in_main:
            return
        if tag in {"h2", "h3", "h4", "h5", "h6"}:
            self.heading = []
        elif tag in {"p", "li"}:
            if self.block_stack:
                self.block_stack[-1][2] = True
            self.block_stack.append([tag, [], False, self.section])
        elif tag == "table":
            if self.table is not None:
                raise ProbeError("unsupported_nested_table")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1][0] != tag:
            raise ProbeError("invalid_document_structure")
        _, hidden, in_main = self.stack.pop()
        if hidden or not in_main:
            return
        if tag in {"h2", "h3", "h4", "h5", "h6"} and self.heading is not None:
            level = int(tag[1])
            self.headings = {n: title for n, title in self.headings.items() if n < level}
            self.headings[level] = normalize(" ".join(self.heading))
            self.section = tuple(self.headings[n] for n in sorted(self.headings))
            self.heading = None
        elif tag in {"p", "li"} and self.block_stack:
            _, parts, has_child, section = self.block_stack.pop()
            if not has_child:
                self.blocks.append((section, normalize(" ".join(parts))))
        elif tag in {"td", "th"} and self.cell is not None:
            self.row.append(normalize(" ".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append((self.section, self.table))
            self.table = None

    def handle_data(self, data):
        if self.stack and not self.stack[-1][1] and self.stack[-1][2]:
            for block in self.block_stack:
                block[1].append(data)
            if self.heading is not None:
                self.heading.append(data)
            if self.cell is not None:
                self.cell.append(data)


def table_value(doc, section, header, label, column, expected):
    # Bind value to its current section and plan column, not a historical table.
    section = (section,) if isinstance(section, str) else section
    tables = [t for sec, t in doc.tables if sec == section and t and t[0] == header]
    if len(tables) != 1:
        raise ProbeError("limits_table_changed")
    rows = [r for r in tables[0][1:] if r and r[0] == label]
    if len(rows) != 1 or len(rows[0]) != len(header) or rows[0][column] != expected:
        raise ProbeError("limits_value_changed")


def clause(doc, section, pattern):
    section = (section,) if isinstance(section, str) else section
    matches = [text for sec, text in doc.blocks if sec == section and re.fullmatch(pattern, text)]
    if len(matches) != 1:
        raise ProbeError("limits_clause_changed")


def verify(name, raw):
    doc = Document(raw)
    if name == "pages":
        table_value(doc, "Builds", ["", "Free", "Pro", "Business"], "Builds per month", 1, "500")
    elif name == "workers":
        table_value(
            doc,
            "Account plan limits",
            ["Feature", "Workers Free", "Workers Paid"],
            "Requests",
            1,
            "100,000/day",
        )
        clause(
            doc,
            "Daily requests",
            r"Accounts on the Workers Free plan have a daily request limit of "
            r"100,000 requests, resetting at midnight UTC\. When a Worker exceeds this limit, "
            r"Cloudflare returns Error 1027\.",
        )
    elif name == "firestore":
        for label, value in (
            ("Stored data", "1 GiB"),
            ("Document reads", "50,000 per day"),
            ("Document writes", "20,000 per day"),
            ("Document deletes", "20,000 per day"),
            ("Outbound data transfer", "10 GiB per month"),
        ):
            table_value(doc, "Free quota", ["Free tier", "Quota"], label, 1, value)
        clause(
            doc, "Free quota", r"Quotas are applied daily and reset around midnight Pacific time\."
        )
        clause(
            doc,
            "Free quota",
            r"(?:Important: )?Cloud Firestore allows exactly one free database per project\.",
        )
    elif name == "github":
        header = [
            "Plan",
            "Artifact storage",
            "Minutes (per month)",
            "Cache storage (per repository)",
            "Custom image storage",
        ]
        for plan in (
            "GitHub Free",
            "GitHub Pro",
            "GitHub Free for organizations",
            "GitHub Team",
            "GitHub Enterprise Cloud",
        ):
            table_value(doc, "Free use of GitHub Actions", header, plan, 3, "10 GB")
        clause(
            doc,
            ("How storage billing works", "Storage measurement units"),
            r"1 GB = 2\^30 bytes = 1,073,741,824 bytes",
        )
        clause(
            doc,
            "How storage billing works",
            r"Shared storage: Actions artifacts and GitHub Packages storage "
            r"share the same pooled allowance\. See GitHub Packages billing\.",
        )
        clause(
            doc,
            "How storage billing works",
            r"Cache storage: Actions cache storage is a separate allowance "
            r"of 10 GB per repository\. Cache storage is not shared "
            r"with artifacts or GitHub Packages\.",
        )
        clause(
            doc,
            "How storage billing works",
            r"Monthly total: Your bill reflects the total storage used "
            r"throughout the month, measured in GB-Hours",
        )
    else:
        raise ProbeError("unknown_limits_source")


class PublicClient:
    def __init__(self, *, opener=None, clock=time.monotonic):
        self.opener = opener or build_opener(NoRedirect)
        self.clock = clock
        self.deadline = clock() + 90

    def read(self, name):
        if name not in SOURCES:
            raise ProbeError("unknown_limits_source")
        if self.clock() >= self.deadline:
            raise ProbeError("request_budget_exhausted")
        # This request never carries account credentials, cookies or provider tokens.
        request = Request(
            SOURCES[name], headers={"Cache-Control": "no-cache", "Accept": "text/html"}
        )
        try:
            with self.opener.open(
                request, timeout=min(15, self.deadline - self.clock())
            ) as response:
                if response.status != 200:
                    raise ProbeError("incomplete_document_response")
                if response.headers.get_content_type() != "text/html":
                    raise ProbeError("unexpected_document_type")
                raw = bytearray()
                while True:
                    if self.clock() >= self.deadline:
                        raise ProbeError("request_budget_exhausted")
                    part = response.read1(min(65536, MAX_BYTES + 1 - len(raw)))
                    if not part:
                        return bytes(raw)
                    raw.extend(part)
                    if len(raw) > MAX_BYTES:
                        raise ProbeError("response_too_large")
        except HTTPError as error:
            raise ProbeError(f"http_{error.code}") from None
        except (HTTPException, URLError, OSError, ValueError):
            raise ProbeError("transport_failed") from None


def audit(now, client=None):
    client = client or PublicClient()
    checks = []
    for name, url in SOURCES.items():
        try:
            raw = client.read(name)
            verify(name, raw)
            checks.append(
                {
                    "source": name,
                    "url": url,
                    "status": "verified",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        except ProbeError as error:
            checks.append({"source": name, "url": url, "status": str(error)})
        except (ValueError, TypeError, AttributeError, RecursionError):
            checks.append({"source": name, "url": url, "status": "invalid_document"})
    result = {"status": "BLOCKED", "checks": checks}
    if all(check["status"] == "verified" for check in checks):
        result.update(status="OK", limits_checked_at=now.isoformat(), limits=dict(LIMITS))
    return result


def main():
    result = audit(datetime.now(UTC))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
