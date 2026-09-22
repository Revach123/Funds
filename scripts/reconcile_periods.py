# -*- coding: utf-8 -*-
"""
reconcile_periods.py — תיקון חד-פעמי (אחרי backfill) של period שגוי.

הרקע: fetch_k203_history.py גזר במקור את period רק מכותרת הדוח (title),
מה שנשבר על דוחות מתקנים ("דוח חודשי-דצמבר 2025 - תיקון דוח") - ה-'-'
הנוסף גרם לפירוש שגוי (נפל בחזרה לחודש הסריקה במקום התקופה האמיתית).
תוקן ב-fetch_k203_history.py (derive_period_from_rows, מבוסס על עמודת
"תאריך דוח" *בתוך* הדוח עצמו - סמכותי, לא תלוי בניסוח הכותרת) - אבל התיקון
חל רק על דוחות שנשלפים *מעכשיו*. הסקריפט הזה מתקן בדיעבד את כל מה שכבר
נשלף עם הלוגיקה הישנה: עובר על reports/**/*.json, גוזר מחדש את התקופה
האמיתית מתוך הנתונים, ואם היא שונה מזו שנשמרה - מעביר את הקובץ למקום
הנכון (ולפעמים מגלה זוג מקורי+תיקון שהיה אמור להיות מקובץ יחד לפי
company+period אבל לא היה, כי נפלו תחת תקופות שונות).

בסוף בונה מחדש את state/canonical_by_period.json מאפס לפי התקופות
המתוקנות (לא רק מעדכן - כדי לתפוס גם זוגות שהתגלו כתוצאה מהתיקון).

לא צריך רשת (עובד רק על קבצים מקומיים שכבר נשלפו).
"""
import json
import os
import sys
from collections import Counter, defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "reports")
STATE_DIR = os.path.join(REPO_ROOT, "state")
CANONICAL_PATH = os.path.join(STATE_DIR, "canonical_by_period.json")


def log(*a):
    print(*a, file=sys.stderr)
    sys.stderr.flush()


def derive_period_from_rows(columns, rows, fallback_ym):
    try:
        idx = columns.index("תאריך דוח")
    except ValueError:
        return fallback_ym
    vals = [r[idx] for r in rows if idx < len(r) and r[idx] and r[idx].isdigit() and len(r[idx]) == 8]
    if not vals:
        return fallback_ym
    most_common = Counter(vals).most_common(1)[0][0]
    dd, mm, yyyy = most_common[0:2], most_common[2:4], most_common[4:8]
    try:
        return f"{int(yyyy):04d}-{int(mm):02d}"
    except ValueError:
        return fallback_ym


def find_report_files():
    for company_dir in sorted(os.listdir(OUT_DIR)):
        cdir = os.path.join(OUT_DIR, company_dir)
        if not os.path.isdir(cdir):
            continue
        for fname in sorted(os.listdir(cdir)):
            if fname.endswith(".json"):
                yield os.path.join(cdir, fname)


def main():
    files = list(find_report_files())
    log(f"נמצאו {len(files)} קבצי דוח לבדיקה")

    fixed = 0
    checked = 0
    errors = 0
    # company -> period -> list of report_id (אחרי תיקון) - לבניית canonical מחדש
    by_company_period = defaultdict(lambda: defaultdict(list))

    for fp in files:
        checked += 1
        if checked % 200 == 0:
            log(f"  ...{checked}/{len(files)}")
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:  # noqa: BLE001
            log(f"  ✗ שגיאת קריאה ב-{fp}: {e}")
            errors += 1
            continue

        stored_period = d.get("period")
        true_period = derive_period_from_rows(d.get("columns") or [], d.get("rows") or [], stored_period)
        company = d["company"]
        rid = d["report_id"]

        if true_period != stored_period:
            new_path = os.path.join(OUT_DIR, os.path.basename(os.path.dirname(fp)),
                                     f"{rid}_{true_period.replace('-', '')}.json")
            log(f"  ⚠ {rid} ({company}): period {stored_period} -> {true_period} "
                f"({os.path.relpath(fp, REPO_ROOT)} -> {os.path.relpath(new_path, REPO_ROOT)})")
            d["period"] = true_period
            d["period_corrected_from"] = stored_period
            with open(new_path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
            if new_path != fp:
                os.remove(fp)
            fixed += 1

        by_company_period[company][true_period].append(rid)

    log(f"\nנבדקו {checked} קבצים, תוקנו {fixed}, שגיאות {errors}")

    # בניית canonical_by_period.json מאפס לפי התקופות המתוקנות
    canonical = {}
    multi_count = 0
    for company, periods in by_company_period.items():
        for period, ids in periods.items():
            ids = sorted(set(ids))
            key = f"{company}|{period}"
            canonical[key] = {"canonical_id": max(ids), "all_ids": ids}
            if len(ids) > 1:
                multi_count += 1

    os.makedirs(STATE_DIR, exist_ok=True)
    with open(CANONICAL_PATH, "w", encoding="utf-8") as f:
        json.dump(canonical, f, ensure_ascii=False, separators=(",", ":"))
    log(f"נבנה מחדש state/canonical_by_period.json: {len(canonical)} צירופי חברה+תקופה, "
        f"{multi_count} עם יותר מדוח אחד")


if __name__ == "__main__":
    main()
