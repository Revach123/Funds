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

_MARKS = "‏‎‪‫‬‭‮ "


def _clean(s):
    if s is None:
        return ""
    s = str(s)
    for m in _MARKS:
        s = s.replace(m, "")
    return s.strip()


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


def fetch_recent_reports(session, n=5, free_text="דוח חודשי"):
    frm, to = date_bounds()
    body = {"pageNumber": 1, "fromDate": frm, "toDate": to,
            "noMeetings": False, "isSingle": False, "isIntendToTaseMember": False,
            "by": "company", "freeText": free_text, "limit": n, "offset": 0}
    r = session.post(LIST_URL, headers=HEADERS, data=json.dumps(body), timeout=30)
    r.raise_for_status()
    return r.json() or []


def fetch_meta(session, report_id):
    hdrs = {k: v for k, v in HEADERS.items() if k != "content-type"}
    r = session.get(META_URL.format(id=report_id), headers=hdrs, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_txt1(session, report_id, url_path):
    hdrs = {k: v for k, v in HEADERS.items() if k != "content-type"}
    full_url = "https://mayafiles.tase.co.il/" + url_path.lstrip("/")
    r = session.get(full_url, headers=hdrs, timeout=60)
    r.raise_for_status()
    return r.content


def main():
    if requests is None:
        log("שגיאה: requests לא מותקן")
        sys.exit(1)
    session = requests.Session()

    candidates = ["דוח", "מבנה נכסים", "פירוט נכסים", "רשימת נכסים",
                  "נכסי הקרן", "ק203", "203"]
    all_reports = []
    seen_ids = set()
    for q in candidates:
        log(f"שלב 1: משיכת רשימת דוחות עם freeText={q!r}...")
        try:
            reps = fetch_recent_reports(session, n=30, free_text=q)
        except Exception as e:
            log(f"  שגיאה עבור {q!r}: {e}")
            continue
        log(f"  {len(reps)} תוצאות")
        for rep in reps:
            if rep.get("id") not in seen_ids:
                seen_ids.add(rep.get("id"))
                all_reports.append(rep)
        time.sleep(0.3)
    log(f"\nסה\"כ דוחות ייחודיים שנאספו מכל השאילתות: {len(all_reports)}")
    titles = {}
    for rep in all_reports:
        t = (rep.get("title") or "").split("-")[0].strip()
        titles.setdefault(t, []).append(rep.get("id"))
    log("\nכותרות ייחודיות (title, לפני ה-'-') וכמה דוגמאות id לכל אחת:")
    for t, ids in sorted(titles.items(), key=lambda kv: -len(kv[1])):
        log(f"  {t!r}: {len(ids)} דוחות, לדוגמה {ids[:3]}")

    out = {"unfiltered_reports_found": len(all_reports),
           "titles": {t: len(ids) for t, ids in titles.items()},
           "samples": []}

    # דגימת מטא-דאטה + formId + attachments לכל כותרת ייחודית (דוח אחד לכל סוג)
    seen_titles = set()
    for rep in all_reports:
        t = (rep.get("title") or "").split("-")[0].strip()
        if t in seen_titles:
            continue
        seen_titles.add(t)
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
        form_id = meta.get("formId")
        report_type = meta.get("reportType")
        log(f"  formId={form_id!r} reportType={report_type!r} attachments={atts}")
        out["samples"].append({
            "report_id": rid, "title": title, "companies": companies,
            "formId": form_id, "reportType": report_type, "attachments": atts,
        })
        time.sleep(0.3)

    print(json.dumps(out, ensure_ascii=False, indent=2))

    # שלב 3: השערה - TXT1 של ק203 (דוח חודשי) הוא בעצם הקובץ המפורט המלא
    # (28 עמודות, שורה לכל נייר), ו-exposure.py רק מסנן/שומר 4 מהעמודות
    # ו-7 קודים. בודקים את זה ישירות מול הכותרת שקיבלנו מהמשתמש.
    log(f"\n(דיבוג) formId גולמיים: {[repr(s.get('formId')) for s in out['samples']]}")
    k203 = next((s for s in out["samples"] if _clean(s.get("formId")) == "ק203"), None)
    if k203 and k203["attachments"]:
        att = k203["attachments"][0]
        log(f"\nשלב 3: הורדת TXT1 בפועל לדוח {k203['report_id']} ({att['url']})...")
        try:
            content = fetch_txt1(session, k203["report_id"], att["url"])
            text = content.decode("utf-8-sig", errors="replace")
            lines = text.splitlines()
            log(f"  {len(lines)} שורות, {len(content)} bytes")
            log(f"  כותרת: {lines[0] if lines else '(ריק)'}")
            log(f"  שורה 2: {lines[1] if len(lines) > 1 else '(אין)'}")
            header_cols = lines[0].split(",") if lines else []
            log(f"  מספר עמודות בכותרת: {len(header_cols)}")
        except Exception as e:
            log(f"  שגיאה: {e}")


if __name__ == "__main__":
    main()
