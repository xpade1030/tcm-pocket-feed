#!/usr/bin/env python3
"""找出所有「(NNN年度公告版)」計畫公告，對每個計畫的最新版（近 15 個月內發布）與前一年度版跑 plan_diff.py。
已比對過的（docs/plan-diffs.json 已有）跳過。"""
import json, logging, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("plan_diffs_all")
VER = re.compile(r"「(.+?)」\s*[（(](\d{3})年度公告版[)）]")


def main():
    notices = json.loads((ROOT / "docs/notices.json").read_text(encoding="utf-8"))["notices"]
    done = json.loads((ROOT / "docs/plan-diffs.json").read_text(encoding="utf-8")) if (ROOT / "docs/plan-diffs.json").exists() else {}
    since = (datetime.now(timezone(timedelta(hours=8))) - timedelta(days=456)).strftime("%Y-%m-%d")
    plans = {}
    for n in notices:
        m = VER.search(n["title"])
        if m and any(a["url"].lower().endswith(".pdf") for a in n.get("attachments") or []):
            plans.setdefault(m.group(1).replace("患者", "病人"), {})[int(m.group(2))] = n   # 計畫名稱歷年用字不一
    for name, vers in plans.items():
        years = sorted(vers)
        if len(years) < 2:
            continue
        new, old = vers[years[-1]], vers[years[-2]]
        if (new.get("date") or "") < since or new["id"] in done:
            continue
        log.info("比對 %s：%d 年版（%s）vs %d 年版（%s）", name, years[-1], new["id"], years[-2], old["id"])
        r = subprocess.run([sys.executable, str(ROOT / "scripts/plan_diff.py"), "--new", new["id"], "--old", old["id"]],
                           capture_output=True, text=True)
        log.info(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "（無輸出）")


if __name__ == "__main__":
    main()
