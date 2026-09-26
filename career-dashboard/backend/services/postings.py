"""Canonical posting identities retain requisition IDs and remove only tracking."""

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote

TRACKING = {
    "source",
    "src",
    "ref",
    "refid",
    "trackingid",
    "trk",
    "trkemail",
    "gh_src",
    "lever-source",
    "lever-origin",
    "fbclid",
    "gclid",
    "midtoken",
    "midsig",
    "otptoken",
    "lipi",
    "eid",
    "lang",
    "locale",
}


def canonical_url(value):
    p = urlsplit(value.strip())
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Enter a public http(s) posting URL without credentials")
    host = p.hostname.lower().removeprefix("www.")
    path = unquote(p.path).rstrip("/") or "/"
    if host == "linkedin.com":
        m = re.search(r"/jobs/view/(\d+)", path)
        if m:
            return "https://www.linkedin.com/jobs/view/" + m[1]
    query = sorted(
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING
    )
    return urlunsplit(("https", host, path, urlencode(query), ""))


def posting_key(url, company="", requisition=""):
    clean = canonical_url(url)
    if requisition.strip():
        return (
            "req:"
            + re.sub(r"\W+", "", company.lower())
            + ":"
            + requisition.strip().lower()
        )
    p = urlsplit(clean)
    q = dict(parse_qsl(p.query))
    for k in ("job", "jobId", "jobid", "requisitionId", "requisition", "gh_jid"):
        if q.get(k):
            return p.hostname + ":" + q[k].lower()
    return clean
