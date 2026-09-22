# -*- coding: utf-8 -*-
"""
fetch_k203_history.py — משיכת כל היסטוריית דוחות ק203 ("דוח חודשי") ממאיה,
כולל הקובץ המפורט ברמת הנייר הבודד (לא רק סיכום מצומצם כמו
scripts/funds_info/exposure.py בריפו Revach).

מקור: אותו endpoint שכבר מוכח עובד ב-Revach:
  POST maya.tase.co.il/api/v1/reports/mutual-funds  (freeText="דוח חודשי")
  -> GET  maya.tase.co.il/api/v1/reports/{id}          (metadata - attachments)
  -> GET  mayafiles.tase.co.il/<attachment txt1 url>   (התוכן עצמו)

אומת ב-scripts/discover_report_attachments.py (2026-09-22): ה-title
"דוח חודשי" תמיד formId=ק203, וה-attachment היחיד (fileType=txt1) הוא
בדיוק הקובץ המפורט ברמת הנייר הבודד (28 עמודות, TAB-delimited, שורה לכל
נייר בכל קרן של החברה) - זהה לקובץ הדוגמה שהמשתמש סיפק.

שמירה: JSON קומפקטי לכל דוח (columns משותף + rows כרשימת-רשימות, לא
מילון לשורה - חוסך פי 2-3 בגודל) תחת:
  reports/<company_safe>/<report_id>_<period_yyyymm>.json
period הוא התקופה שהדוח מדווח עליה (מנותח מהכותרת, למשל "אפריל 2026"),
לא החודש שבו נמצא/סרקנו אותו - דוח יכול להתפרסם או להיות מתוקן חודשים
אחרי התקופה עצמה.

יש בדרך כלל יותר מדוח אחד לאותה חברה+תקופה (מקורי + תיקון/ים) - נראה
בפועל, למשל "קסם" עם 3 דוחות שונים לאותה "דוח חודשי-אפריל 2026". בלי
לדעת עדיין את הסמנטיקה המדויקת של שדה ה-correctives במטא-דאטה (נשמר גולמי
בכל קובץ, ל-analysis עתידי), state/canonical_by_period.json עוקב אחרי כל
ה-report_id-ים לכל צירוף חברה+תקופה ומסמן את ה-report_id הגבוה ביותר
(=הוגש מאוחר יותר) כ-canonical_id - כל הדוחות עצמם נשמרים בדיסק (לא נמחק
מידע), רק שממתי צריך "את הגרסה הנכונה" צריך לסנן לפי canonical_id.

מניפסט התקדמות state/fetched_report_ids.json + state/scanned_months.json
כדי שריצות חוזרות ימשיכו במקום להתחיל מחדש (יש כנראה אלפי דוחות על פני
שנים - ריצה אחת לא מספיקה).

תקציב זמן לריצה: עוצר לאחר RUN_BUDGET_SECONDS (משאיר מרווח ל-commit/push
לפני timeout של ה-job) - להריץ שוב (ידנית או בקרון) עד שההיסטוריה מכוסה.

מצב יומי (DAILY_RECENT_MONTHS=N, ר' .github/workflows/fetch-k203-daily.yml):
לתחזוקה שוטפת אחרי שההיסטוריה מכוסה - סורק תמיד מחדש את N החודשים
האחרונים בלי תלות ב-scanned_months (כדי לתפוס דוחות חדשים של החודש הנוכחי
+ תיקונים מאוחרים לחודשים קודמים), אבל עדיין מדלג על report_id שכבר יש
ב-fetched_ids - כך שריצה יומית מהירה גם אם רוב הדוחות כבר קיימים.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

try:
    import requests
except ImportError:
    requests = None

BASE = "https://maya.tase.co.il"
FILES_BASE = "https://mayafiles.tase.co.il/"
LIST_URL = BASE + "/api/v1/reports/mutual-funds"
ETF_LIST_URL = BASE + "/api/v1/reports/etfs"
# שני endpoints נפרדים במאיה (רשימות "דוחות" שונות לקרנות סל מול קרנות
# נאמנות - לבקשת המשתמש, לא לסמוך על כך ש-mutual-funds מכסה הכל תמיד;
# נבדק פעם אחת (discover_etf_vs_mutual.py) שבחודש לדוגמה etfs היה תת-קבוצה
# מלאה, אבל זו לא הוכחה לכל טווח ההיסטוריה - שואבים משניהם ומאחדים).
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "reports")
STATE_DIR = os.path.join(REPO_ROOT, "state")
FETCHED_IDS_PATH = os.path.join(STATE_DIR, "fetched_report_ids.json")
SCANNED_MONTHS_PATH = os.path.join(STATE_DIR, "scanned_months.json")
CANONICAL_PATH = os.path.join(STATE_DIR, "canonical_by_period.json")

# נבדק בפועל (ריצה מלאה 2013-01 עד 2020-11): 0 דוחות בכל חודש - הפורמט הזה
# כנראה לא היה בשימוש/מאוחסן במאיה לפני דצמבר 2020. HISTORY_START מתחיל שם
# במקום מ-2013 כדי לא לבזבז מאות בקשות ריקות; ניתן לעקוף עם env var אם
# בעתיד מתגלה שיש בכל זאת נתונים מוקדמים יותר.
_hs = os.environ.get("HISTORY_START")
HISTORY_START = date(*(int(x) for x in _hs.split("-"))) if _hs else date(2020, 12, 1)

HEB_MONTHS = {"ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
              "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12}


def parse_period(title, fallback_ym):
    """'דוח חודשי-אפריל 2026' -> '2026-04' (התקופה שהדוח *מדווח עליה*,
    לא החודש שבו הוא נמצא/פורסם - דוח יכול להתפרסם/להיות מתוקן חודשים
    אחרי התקופה עצמה - זה מה ש-fallback_ym (חודש הסריקה) היה מייצג בטעות)."""
    t = _clean(title)
    parts = t.split("-")
    if len(parts) < 2:
        return fallback_ym
    tail = _clean(parts[-1])
    year = mon = None
    for tok in tail.split():
        if tok.isdigit() and len(tok) == 4:
            year = int(tok)
        elif tok in HEB_MONTHS:
            mon = HEB_MONTHS[tok]
    if year and mon:
        return f"{year:04d}-{mon:02d}"
    return fallback_ym

RUN_BUDGET_SECONDS = int(os.environ.get("RUN_BUDGET_SECONDS") or 3 * 60 * 60)   # 3h ברירת מחדל - ריפו ציבורי, דקות Actions חינם; משאיר מרווח לפני timeout-minutes של ה-job
SLEEP_BETWEEN_CALLS = 0.7   # מאיה חוסמת (403) אחרי סדרה מהירה מדי של בקשות

_MARKS = "‏‎‪‫‬‭‮ "


def log(*a):
    print(*a, file=sys.stderr)
    sys.stderr.flush()


def _clean(s):
    if s is None:
        return ""
    s = str(s)
    for m in _MARKS:
        s = s.replace(m, "")
    return s.strip()


def safe_name(s):
    s = _clean(s)
    s = re.sub(r"[^\w\-א-ת]+", "_", s, flags=re.UNICODE).strip("_")
    return s or "unknown"


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def _run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, **kw)


def git_commit_push(message):
    """Commit+push תחת reports/ ו-state/ אחרי כל חודש שהושלם, במקום commit
    ענק אחד בסוף כל הריצה - כך שאם הריצה נכשלת/נחתכת באמצע, כל מה שכבר
    הורד נשאר בגיט, ולא צריך לחכות לכל התקציב לפני שרואים push ראשון.
    לא "מקביל" ל-fetch (זה תהליך פייתון יחיד סינכרוני), אבל מפזר את עלות
    ה-git add/commit/push על פני הריצה במקום גוש אחד בסוף."""
    _run(["git", "add", "reports", "state"])
    diff = _run(["git", "diff", "--staged", "--quiet"])
    if diff.returncode == 0:
        return  # אין שינויים
    _run(["git", "commit", "-m", message])
    for attempt in range(1, 6):
        push = _run(["git", "push"])
        if push.returncode == 0:
            return
        log(f"  ⚠ git push נדחה (נסיון {attempt}) - מבצע rebase ומנסה שוב: {push.stderr.strip()[:200]}")
        _run(["git", "pull", "--rebase", "origin", "main"])
    log("  ✗ git push נכשל אחרי 5 נסיונות - הריצה הבאה/ה-workflow-level commit ינסו שוב")


def month_windows(start, end):
    """['2013-01', '2013-02', ...] עד החודש הנוכחי (כולל)."""
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def month_bounds(ym):
    y, m = (int(x) for x in ym.split("-"))
    frm = date(y, m, 1)
    to = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    z_frm, z_to = "T00:00:00.000Z", "T00:00:00.000Z"
    return frm.isoformat() + z_frm, to.isoformat() + z_to


class Blocked(Exception):
    """מאיה החזירה 403 אחרי backoff ארוך - כנראה חסימת קצב זמנית. עוצרים
    את כל הריצה (לא רק החודש הנוכחי) כדי לא לבזבז תקציב על עוד 403-ים."""


def _is_rate_limit(e):
    return isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 403


def _request_with_retries(fn, what):
    """fn() -> Response. 403 מקבל backoff ארוך משלו (60s) לפני שנכנע ומסמן Blocked;
    שגיאות אחרות - backoff קצר רגיל (1s, 3s)."""
    last = None
    for attempt in (1, 2, 3):
        try:
            r = fn()
            _ = r.content
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if _is_rate_limit(e):
                if attempt >= 2:
                    raise Blocked(f"{what}: 403 אחרי {attempt} נסיונות - מאיה חוסמת") from e
                log(f"  ⚠ {what}: 403 (חסימת קצב חשודה) - ממתין 60s ומנסה שוב")
                time.sleep(60)
                continue
            if attempt < 3:
                wait = 1 if attempt == 1 else 3
                log(f"  ⚠ {what}: נסיון {attempt} נכשל ({type(e).__name__}: {e}); ממתין {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"{what} נכשל אחרי 3 נסיונות: {last}") from last


def _post_with_retries(session, url, body, timeout=30, what="request"):
    return _request_with_retries(
        lambda: session.post(url, headers=HEADERS, data=json.dumps(body), timeout=timeout), what)


def _get_with_retries(session, url, timeout=60, what="request"):
    hdrs = {k: v for k, v in HEADERS.items() if k != "content-type"}
    return _request_with_retries(lambda: session.get(url, headers=hdrs, timeout=timeout), what)


def _list_from_endpoint(session, list_url, ym, label):
    """כל דוחות ק203 ('דוח חודשי') בחודש נתון מ-endpoint אחד, עם דפדוף מלא."""
    frm, to = month_bounds(ym)
    out, seen, page = [], set(), 1
    while True:
        body = {"pageNumber": page, "fromDate": frm, "toDate": to,
                "noMeetings": False, "isSingle": False, "isIntendToTaseMember": False,
                "by": "company", "freeText": "דוח חודשי", "limit": 30, "offset": (page - 1) * 30}
        r = _post_with_retries(session, list_url, body, what=f"רשימה {ym} ({label}) עמוד {page}")
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
            title = _clean(rep.get("title") or "")
            if not title.startswith("דוח חודשי"):
                continue  # בטיחות - freeText יכול תיאורטית להתאים גם לכותרות אחרות
            for comp in (rep.get("companies") or []):
                out.append({"id": rid, "title": rep.get("title"),
                            "company": _clean(comp.get("name") or "")})
        if new == 0 or len(data) < 30:
            break
        page += 1
        time.sleep(SLEEP_BETWEEN_CALLS)
    return out


def list_month_reports(session, ym):
    """כל דוחות ק203 בחודש נתון, משני ה-endpoints (mutual-funds + etfs) -
    רשימות נפרדות במאיה לקרנות סל מול קרנות נאמנות; מאחדים לפי report id."""
    by_id = {}
    for list_url, label in ((LIST_URL, "mutual-funds"), (ETF_LIST_URL, "etfs")):
        for rep in _list_from_endpoint(session, list_url, ym, label):
            by_id.setdefault(rep["id"], rep)  # הראשון שמגיע קובע (companies זהה בד"כ)
        time.sleep(SLEEP_BETWEEN_CALLS)
    return list(by_id.values())


def fetch_meta(session, report_id):
    r = _get_with_retries(session, META_URL.format(id=report_id), what=f"מטא-דאטה {report_id}")
    return r.json()


def fetch_txt1_rows(session, url_path):
    full_url = FILES_BASE + url_path.lstrip("/")
    r = _get_with_retries(session, full_url, what=f"TXT1 {url_path}")
    text = r.content.decode("utf-8-sig", errors="replace")
    lines = [ln for ln in text.split("\n") if ln.strip("\r\n \t")]
    if not lines:
        return [], []
    columns = [_clean(c) for c in lines[0].split("\t")]
    rows = []
    for ln in lines[1:]:
        parts = ln.rstrip("\r").split("\t")
        if len(parts) < len(columns):
            parts += [""] * (len(columns) - len(parts))
        rows.append([_clean(p) for p in parts[:len(columns)]])
    return columns, rows


def main():
    if requests is None:
        log("שגיאה: requests לא מותקן")
        sys.exit(1)

    fetched_ids = set(load_json(FETCHED_IDS_PATH, []))
    scanned_months = set(load_json(SCANNED_MONTHS_PATH, []))
    # company|period_ym -> {"canonical_id": int, "all_ids": [int,...]} - יתכנו
    # כמה דוחות לאותה חברה+תקופה (דוח מקורי + תיקון/ים); בלי לדעת עדיין את
    # הסמנטיקה המדויקת של שדה ה-correctives במטא-דאטה, ברירת המחדל השמרנית
    # היא: report_id גבוה יותר = הוגש מאוחר יותר = כנראה הגרסה הסופית.
    canonical = load_json(CANONICAL_PATH, {})
    log(f"מצב קיים: {len(fetched_ids)} דוחות שכבר נשלפו, {len(scanned_months)} חודשים שכבר נסרקו, "
        f"{len(canonical)} צירופי חברה+תקופה")

    today = datetime.now(timezone.utc).date()
    all_months = month_windows(HISTORY_START, today)

    daily_recent = int(os.environ.get("DAILY_RECENT_MONTHS") or 0)
    if daily_recent > 0:
        # מצב יומי: תמיד סורקים מחדש את N החודשים האחרונים (לא לפי
        # scanned_months) - כדי לתפוס גם דוחות חדשים לגמרי (החודש הנוכחי
        # עדיין "פתוח") וגם תיקונים מאוחרים לתקופות שכבר "הושלמו". fetched_ids
        # עדיין מונע הורדה חוזרת של דוח שכבר יש לנו - רק חדשים/מתוקנים
        # מתווספים בפועל.
        pending_months = all_months[-daily_recent:]
        log(f"מצב יומי: סורק מחדש את {len(pending_months)} החודשים האחרונים "
            f"({', '.join(pending_months)}), ללא תלות ב-scanned_months")
    else:
        pending_months = [m for m in all_months if m not in scanned_months]
        log(f"סה\"כ {len(all_months)} חודשים בטווח ({HISTORY_START} עד {today}), "
            f"{len(pending_months)} עדיין לא נסרקו")

    session = requests.Session()
    t0 = time.monotonic()
    stats = {"months_scanned": 0, "reports_found": 0, "reports_fetched": 0,
             "reports_skipped_existing": 0, "errors": 0}

    def budget_left():
        return time.monotonic() - t0 < RUN_BUDGET_SECONDS

    # checkpoint גם באמצע חודש (לא רק כשהוא מסתיים) - חודש יכול לקחת כמה
    # דקות (15-40+ דוחות), וריצה שנעצרת/מבוטלת באמצע חודש בלי checkpoint
    # כזה מאבדת את כל מה שהורד בו (נראה בפועל: run #6 בוטל אחרי דקה, בלי
    # אף commit, כי עוד לא סיים אף חודש שלם).
    CHECKPOINT_EVERY_REPORTS = 5
    CHECKPOINT_EVERY_SECONDS = 45
    last_checkpoint_t = time.monotonic()
    reports_since_checkpoint = 0

    def checkpoint(force=False):
        nonlocal last_checkpoint_t, reports_since_checkpoint
        if not force and reports_since_checkpoint < CHECKPOINT_EVERY_REPORTS \
                and time.monotonic() - last_checkpoint_t < CHECKPOINT_EVERY_SECONDS:
            return
        save_json(FETCHED_IDS_PATH, sorted(fetched_ids))
        save_json(SCANNED_MONTHS_PATH, sorted(scanned_months))
        save_json(CANONICAL_PATH, canonical)
        git_commit_push(f"k203: checkpoint ({len(fetched_ids)} total reports)")
        last_checkpoint_t = time.monotonic()
        reports_since_checkpoint = 0

    blocked = False
    for ym in pending_months:
        if not budget_left():
            log(f"תקציב הזמן ({RUN_BUDGET_SECONDS}s) נגמר - עוצר, ריצה הבאה תמשיך מכאן")
            break
        log(f"\n=== סורק חודש {ym} ===")
        try:
            reports = list_month_reports(session, ym)
        except Blocked as e:
            log(f"  {e} - עוצר את כל הריצה (לא רק החודש), כדי לא לבזבז תקציב על עוד חסימות")
            blocked = True
            break
        except Exception as e:
            log(f"  שגיאה בסריקת {ym}: {e}")
            stats["errors"] += 1
            continue
        log(f"  {len(reports)} דוחות ק203 נמצאו")
        stats["reports_found"] += len(reports)

        month_done = True
        for rep in reports:
            if not budget_left():
                month_done = False
                break
            rid = rep["id"]
            if rid in fetched_ids:
                stats["reports_skipped_existing"] += 1
                continue
            try:
                meta = fetch_meta(session, rid)
                atts = meta.get("attachments") or []
                txt1 = next((a for a in atts if (a.get("fileType") or "").lower() == "txt1"), None)
                if not txt1 or not txt1.get("url"):
                    log(f"  ⚠ דוח {rid}: אין attachment מסוג txt1, מדלג")
                    fetched_ids.add(rid)  # לא ננסה שוב - זה קבוע למבנה הדוח
                    continue
                columns, rows = fetch_txt1_rows(session, txt1["url"])
                company = rep["company"]
                period_ym = parse_period(rep["title"], ym)
                out_path = os.path.join(OUT_DIR, safe_name(company),
                                         f"{rid}_{period_ym.replace('-', '')}.json")
                save_json(out_path, {
                    "report_id": rid, "company": company, "title": rep["title"],
                    "form_id": "ק203", "period": period_ym, "scanned_in_month": ym,
                    "publish_date": meta.get("publishDate"), "is_priority": meta.get("isPriority"),
                    "correctives": meta.get("correctives"), "comment": meta.get("comment"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "source_url": FILES_BASE + txt1["url"],
                    "columns": columns, "rows": rows,
                })
                fetched_ids.add(rid)
                stats["reports_fetched"] += 1
                reports_since_checkpoint += 1

                key = f"{company}|{period_ym}"
                entry = canonical.setdefault(key, {"canonical_id": rid, "all_ids": []})
                if rid not in entry["all_ids"]:
                    entry["all_ids"].append(rid)
                if rid > entry["canonical_id"]:
                    entry["canonical_id"] = rid
                dup_note = f" (⚠ {len(entry['all_ids'])} דוחות לתקופה הזו - all_ids={entry['all_ids']})" \
                    if len(entry["all_ids"]) > 1 else ""
                log(f"  ✓ {rid} ({company}, תקופה {period_ym}): {len(rows)} שורות "
                    f"-> {os.path.relpath(out_path, REPO_ROOT)}{dup_note}")
                checkpoint()
            except Blocked as e:
                log(f"  {e} - עוצר את כל הריצה")
                blocked = True
                month_done = False
                break
            except Exception as e:  # noqa: BLE001
                log(f"  ✗ דוח {rid} ({rep['company']}) נכשל: {e}")
                stats["errors"] += 1
            time.sleep(SLEEP_BETWEEN_CALLS)

        if month_done:
            scanned_months.add(ym)
            stats["months_scanned"] += 1
            checkpoint(force=True)
        else:
            log(f"  חודש {ym} לא הושלם (תקציב זמן/חסימה) - יושלם בריצה הבאה")
            break
        if blocked:
            break

    checkpoint(force=True)
    multi = {k: v for k, v in canonical.items() if len(v["all_ids"]) > 1}
    if multi:
        log(f"\n⚠ {len(multi)} צירופי חברה+תקופה עם יותר מדוח אחד (מקורי+תיקון/ים) - "
            f"ר' {os.path.relpath(CANONICAL_PATH, REPO_ROOT)} ל-canonical_id מומלץ לכל אחד")

    remaining = len(all_months) - len(scanned_months)
    log(f"\n=== סיכום ריצה ===")
    log(json.dumps({**stats, "months_remaining": remaining, "blocked": blocked,
                     "total_fetched_ids": len(fetched_ids)}, ensure_ascii=False, indent=2))
    if blocked:
        log("\n⚠ מאיה חסמה (403) - עדיף לחכות כמה דקות/שעה לפני ריצה חוזרת, ואולי להגדיל SLEEP_BETWEEN_CALLS עוד")
    elif remaining > 0:
        log(f"\n⚠ נותרו {remaining} חודשים לסריקה - יש להריץ את ה-workflow שוב כדי להמשיך")
    else:
        log("\n✓ כל טווח ההיסטוריה נסרק")


if __name__ == "__main__":
    main()
