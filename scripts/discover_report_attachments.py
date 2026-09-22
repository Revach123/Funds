# -*- coding: utf-8 -*-
"""
discover_report_attachments.py — שלב גילוי חד-פעמי.

מטרה: לאתר את סוג ה-attachment (attachmentType) שמחזיר את קובץ ההחזקות
המפורט ברמת הנייר הבודד (כמו הדוגמה שקיבלנו ידנית) — לעומת TXT1 שכבר
בשימוש ב-scripts/funds_info/exposure.py (ריפו Revach) ומחזיר רק סיכום
לפי קודי "סוג נכס" (300/319/320/326/327/331), לא פירוט לפי נייר.

גישה: אותה רשימת "דוח חודשי" (reports/mutual-funds, freeText) שכבר
מוכחת עובדת ב-exposure.py. לוקחים דוח אחד עדכני, קוראים את המטא-דאטה
שלו (GET /api/v1/reports/{id}) ומדפיסים את מערך ה-attachments המלא —
fileType/attachmentType/url לכל אחד. משם נזהה איזה מהם הוא הקובץ המפורט.

פלט: JSON למסך (נקרא מתוך לוגים של GitHub Actions - אין כאן הרשאת רשת
לבדוק ישירות).
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
LIST_URL = BASE + "/api/v1/reports/mutual-funds"
META_URL = BASE + "/api/v1/reports/{id}"

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


def date_bounds():
    now = datetime.now(timezone.utc)
    today = now.date()
    frm = today - timedelta(days=60)
    z = "T22:00:00.000Z"
    return frm.isoformat() + z, today.isoformat() + z


def fetch_recent_reports(session, n=5):
    frm, to = date_bounds()
    body = {"pageNumber": 1, "fromDate": frm, "toDate": to,
            "noMeetings": False, "isSingle": False, "isIntendToTaseMember": False,
            "by": "company", "freeText": "דוח חודשי", "limit": n, "offset": 0}
    r = session.post(LIST_URL, headers=HEADERS, data=json.dumps(body), timeout=30)
    r.raise_for_status()
    return r.json() or []


def fetch_meta(session, report_id):
    hdrs = {k: v for k, v in HEADERS.items() if k != "content-type"}
    r = session.get(META_URL.format(id=report_id), headers=hdrs, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    if requests is None:
        log("שגיאה: requests לא מותקן")
        sys.exit(1)
    session = requests.Session()

    log("שלב 1: משיכת רשימת דוחות חודשיים אחרונים...")
    reports = fetch_recent_reports(session, n=5)
    log(f"נמצאו {len(reports)} דוחות בדגימה")
    if not reports:
        log("לא נמצאו דוחות כלל — ייתכן שצריך טווח תאריכים אחר")
        print(json.dumps({"reports_found": 0}, ensure_ascii=False))
        return

    out = {"reports_found": len(reports), "samples": []}
    for rep in reports[:3]:
        rid = rep.get("id")
        title = rep.get("title")
        companies = [c.get("name") for c in (rep.get("companies") or [])]
        log(f"\nשלב 2: מטא-דאטה לדוח {rid} ({title})...")
        try:
            meta = fetch_meta(session, rid)
        except Exception as e:
            log(f"  שגיאה: {e}")
            continue
        atts = meta.get("attachments") or []
        log(f"  {len(atts)} attachments:")
        att_summary = []
        for a in atts:
            summary = {k: a.get(k) for k in
                       ("fileType", "attachmentType", "type", "id", "name", "url") if k in a}
            log(f"    {summary}")
            att_summary.append(summary)
        out["samples"].append({
            "report_id": rid, "title": title, "companies": companies,
            "attachments": att_summary, "raw_meta_keys": list(meta.keys()),
        })
        time.sleep(0.3)

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
