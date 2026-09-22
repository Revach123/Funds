# -*- coding: utf-8 -*-
"""
discover_correctives.py — שלב גילוי: איך מאיה מסמנת דוח מתקן (תיקון) מול
המקורי? כבר ראינו 3 דוחות "קסם" עם אותה כותרת בדיוק (דוח חודשי-אפריל 2026)
ואותו מספר שורות/קרנות - כנראה מקורי + תיקון/ים. המטא-דאטה כוללת שדה
"correctives" (נראה ב-discover_report_attachments.py הקודם) - בודקים מה
יש בו בפועל, ואם יש publishDate/isPriority שיעזרו לקבוע איזה הדוח הסופי.
"""
import json
import sys
import time

try:
    import requests
except ImportError:
    requests = None

BASE = "https://maya.tase.co.il"
META_URL = BASE + "/api/v1/reports/{id}"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "he-IL",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"),
    "referer": BASE + "/he/reports/mutual-funds",
}

# 3 דוחות "קסם" זהים בכותרת (דוח חודשי-אפריל 2026) שכבר נשלפו בטעות כ-3 קבצים נפרדים
KNOWN_DUPLICATE_IDS = [1742131, 1741465, 1741756]


def log(*a):
    print(*a, file=sys.stderr)
    sys.stderr.flush()


def main():
    if requests is None:
        sys.exit(1)
    session = requests.Session()
    out = {}
    for rid in KNOWN_DUPLICATE_IDS:
        r = session.get(META_URL.format(id=rid), headers=HEADERS, timeout=30)
        r.raise_for_status()
        meta = r.json()
        out[rid] = meta
        time.sleep(1)
    with open("discover_correctives_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("done")


if __name__ == "__main__":
    main()
