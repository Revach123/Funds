# -*- coding: utf-8 -*-
"""
discover_etf_vs_mutual.py — בדיקה: המשתמש ציין שיש רשימה נפרדת לקרנות סל
מול קרנות נאמנות ("כל אחד מדף אחר"). fetch_k203_history.py שאב עד כה רק
מ-reports/mutual-funds (כמו exposure.py הקיים ב-Revach). האם reports/etfs
(המשמש ב-dnm.py לק155) מחזיר, עם אותו freeText="דוח חודשי", עוד/אחרים
company/report ids שלא ב-mutual-funds? אם כן - צריך לשאוב משני ה-endpoints.

פלט: קובץ JSON מוצא, לא stdout/stderr (לקחי discover_report_attachments.py -
tail של לוגים לא אמין לתפוס פלט מלא/מאוחר).
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    requests = None

BASE = "https://maya.tase.co.il"
MUTUAL_URL = BASE + "/api/v1/reports/mutual-funds"
ETF_URL = BASE + "/api/v1/reports/etfs"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "he-IL",
    "content-type": "application/json",
    "x-maya-with": "allow",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    "referer": BASE + "/he/reports/mutual-funds",
}


def log(*a):
    print(*a, file=sys.stderr)
    sys.stderr.flush()


def date_bounds(days_back=60):
    now = datetime.now(timezone.utc)
    today = now.date()
    frm = today - timedelta(days=days_back)
    z = "T22:00:00.000Z"
    return frm.isoformat() + z, today.isoformat() + z


def fetch_all(session, url, free_text, max_pages=6):
    frm, to = date_bounds()
    out, seen, page = [], set(), 1
    while page <= max_pages:
        body = {"pageNumber": page, "fromDate": frm, "toDate": to,
                "noMeetings": False, "isSingle": False, "isIntendToTaseMember": False,
                "by": "company", "freeText": free_text, "limit": 30, "offset": (page - 1) * 30}
        r = session.post(url, headers=HEADERS, data=json.dumps(body), timeout=30)
        r.raise_for_status()
        data = r.json() or []
        if not data:
            break
        new = 0
        for rep in data:
            rid = rep.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            new += 1
            for comp in (rep.get("companies") or []):
                out.append({"id": rid, "title": rep.get("title"), "company": (comp.get("name") or "").strip()})
        if new == 0 or len(data) < 30:
            break
        page += 1
        time.sleep(0.7)
    return out


def main():
    if requests is None:
        sys.exit(1)
    session = requests.Session()

    log("שולף מ-reports/mutual-funds...")
    mutual = fetch_all(session, MUTUAL_URL, "דוח חודשי")
    log(f"  {len(mutual)} תוצאות")
    time.sleep(1)

    log("שולף מ-reports/etfs...")
    etf = fetch_all(session, ETF_URL, "דוח חודשי")
    log(f"  {len(etf)} תוצאות")

    mutual_ids = {r["id"] for r in mutual}
    etf_ids = {r["id"] for r in etf}
    mutual_companies = sorted({r["company"] for r in mutual})
    etf_companies = sorted({r["company"] for r in etf})

    out = {
        "mutual_funds_endpoint": {"count": len(mutual), "companies": mutual_companies,
                                   "sample_ids": sorted(mutual_ids)[:10]},
        "etfs_endpoint": {"count": len(etf), "companies": etf_companies,
                           "sample_ids": sorted(etf_ids)[:10]},
        "only_in_etf_endpoint_ids": sorted(etf_ids - mutual_ids),
        "only_in_mutual_endpoint_ids": sorted(mutual_ids - etf_ids),
        "overlap_ids": sorted(etf_ids & mutual_ids),
        "companies_only_in_etf": sorted(set(etf_companies) - set(mutual_companies)),
        "companies_only_in_mutual": sorted(set(mutual_companies) - set(etf_companies)),
    }
    with open("discover_etf_vs_mutual_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("done, wrote discover_etf_vs_mutual_results.json")


if __name__ == "__main__":
    main()
