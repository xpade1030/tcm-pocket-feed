#!/usr/bin/env python3
"""補頁碼：為既有的 docs/plan-diffs.json 每點變更加上新版原文所在頁（讀 raw/plan-diff/<新版編號>.txt）。新跑的比對由 plan_diff.py 直接寫入。"""
import json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "docs/plan-diffs.json"
d = json.loads(p.read_text(encoding="utf-8"))
for nid, r in d.items():
    f = ROOT / f"raw/plan-diff/{nid}.txt"
    if not f.exists():
        print(nid, "缺文字檔"); continue
    pages = [re.sub(r"\s+", "", t) for t in f.read_text(encoding="utf-8", errors="ignore").split("\f")]
    for c in r.get("changes", []):
        q = re.sub(r"\s+", "", c.get("quote") or "")
        c["page"] = next((i + 1 for i, t in enumerate(pages) if q and q in t), None)
    print(nid, [c.get("page") for c in r.get("changes", [])])
p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
