#!/usr/bin/env python3
"""抓取中醫師公會全國聯合會（twtm.tw）公告，輸出 docs/notices.json 供 App 下載。

- 列表：new.php?cat=<分類>&p=<頁碼>；內頁：new.php?cat=<分類>&id=<編號>
- 增量更新：已在 notices.json 的公告不重抓內頁（除非 --full）
- raw/ 保存每次抓到的列表 HTML 摘要與 log，方便除錯
只用 Python 標準函式庫（GitHub Actions 不需安裝套件）。
"""
import argparse, html, json, logging, re, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/notices.json"
RAW = ROOT / "raw"
BASE = "https://twtm.tw/"   # www.twtm.tw 憑證有問題，用無 www 網域
# (頁面, 分類代號, 分類名稱)。公告區 new.php；專案計畫專區 project.php（2026-10-08 加入，作者認為最該著重）
SOURCES = [("new", "1", "大眾最新消息"), ("new", "16", "新聞稿"), ("new", "30", "法規專區"), ("new", "76", "會務快訊"),
           ("new", "53", "中醫點值結算說明"), ("new", "69", "下載專區"),
           ("project", "103", "專案：健保署公告名單、六區課程通知"), ("project", "116", "專案：上課名單查詢"),
           ("project", "115", "專案：三高"), ("project", "90", "專案：品質保證保留款院所名單"),
           ("project", "65", "專案：感控暨針灸標準 SOP"), ("project", "86", "專案：照護機構中醫醫療照護"),
           ("project", "51", "專案：第八章特定疾病門診加強照護"), ("project", "83", "專案：中藥用藥安全"),
           ("project", "50", "專案：醫療資源不足地區"), ("project", "44", "專案：西醫住院中醫輔助醫療"),
           ("project", "54", "專案：癌症加強照護整合"), ("project", "73", "專案：急症處置"),
           ("project", "45", "專案：孕產照護"), ("project", "78", "專案：慢性腎臟病"),
           ("project", "74", "專案：居家醫療照護整合"), ("project", "46", "專案：其他表單下載")]
CATS = {f"{pg}:{c}": n for pg, c, n in SOURCES}
PAGES = 10         # 每類抓前幾頁（每頁約 5 則）
KEEP = 150         # 每類最多保留幾則
UA = "Mozilla/5.0 (compatible; tcm-pocket-feed/1.0; +https://github.com/xpade1030/tcm-pocket-feed)"
TW = timezone(timedelta(hours=8))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("twtm")


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="ignore")
        except Exception as e:
            log.warning("抓取失敗（第 %d 次）%s：%s", i + 1, url, e)
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"放棄：{url}")


def text_of(fragment):
    """HTML 片段轉純文字：保留段落換行、去樣式。"""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", fragment)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|h\d)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in s.splitlines()]
    out, blank = [], 0
    for l in lines:
        if not l:
            blank += 1
            if blank == 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(l)
    return "\n".join(out).strip()


NAV_LINK = re.compile(r"^https://twtm\.tw/(new|project)\.php\?cat=\d+$")   # 內頁底部「回列表」


def clean_atts(atts):
    return [a for a in atts if not NAV_LINK.match(a["url"])]


def parse_list(page_html, cat, pg="new"):
    items = []
    for nid, inner in re.findall(rf'href="{pg}\.php\?cat={cat}&(?:amp;)?id=(\d+)"[^>]*>(.*?)</a>', page_html, re.S):
        t = text_of(inner)
        m = re.match(r"(\d{4}-\d{2}-\d{2})\s*(?:\[[^\]]*\])?\s*(.*)", t, re.S)
        if m:
            items.append({"id": nid, "date": m.group(1), "title": m.group(2).strip()})
        elif t:
            items.append({"id": nid, "date": None, "title": t})
    seen, uniq = set(), []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"]); uniq.append(it)
    return uniq


def parse_detail(page_html):
    title = re.search(r'<h3 class="post-title[^"]*">(.*?)</h3>', page_html, re.S)
    when = re.search(r'pe-7s-clock"></i>\s*([\d\- :]+)', page_html)
    start = page_html.find('<ul class="post-meta')
    start = page_html.find("</ul>", start) + 5 if start >= 0 else -1
    end = page_html.find("<!-- BEGIN FOOTER ZONE -->", start) if start > 0 else -1
    body_html = page_html[start:end] if start > 0 and end > start else ""
    atts = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body_html, re.S):
        href = html.unescape(href)
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        url = href if href.startswith("http") else BASE + href.lstrip("./")
        atts.append({"label": text_of(label) or url.rsplit("/", 1)[-1], "url": url})
    return {
        "title": text_of(title.group(1)) if title else None,
        "time": when.group(1).strip() if when else None,
        "body": text_of(body_html)[:6000],
        "attachments": clean_atts(atts)[:30],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="重抓所有內頁")
    args = ap.parse_args()
    RAW.mkdir(exist_ok=True)
    old = {}
    if OUT.exists() and not args.full:
        for n in json.loads(OUT.read_text(encoding="utf-8")).get("notices", []):
            old[n["id"]] = n
    log.info("既有公告 %d 則", len(old))

    notices, new_count = {}, 0
    for pg, cat, cname in SOURCES:
        listed = []
        for p in range(1, PAGES + 1):
            page = get(f"{BASE}{pg}.php?p={p}&cat={cat}")
            items = parse_list(page, cat, pg)
            log.info("[%s] 第 %d 頁 %d 則", cname, p, len(items))
            if not items:
                break
            listed += items
            time.sleep(0.6)
        for it in listed[:KEEP]:
            if it["id"] in old and old[it["id"]].get("body") is not None:
                n = old[it["id"]]
            else:
                d = parse_detail(get(f"{BASE}{pg}.php?cat={cat}&id={it['id']}"))
                n = {"id": it["id"], "category": cname, "cat": cat, "section": "專案計畫專區" if pg == "project" else "公告",
                     "title": d["title"] or it["title"], "date": it["date"] or (d["time"] or "")[:10],
                     "time": d["time"], "body": d["body"], "attachments": d["attachments"],
                     "url": f"{BASE}{pg}.php?cat={cat}&id={it['id']}"}
                new_count += 1
                log.info("  新增 %s %s", n["date"], n["title"][:40])
                time.sleep(0.6)
            n["attachments"] = clean_atts(n.get("attachments") or [])
            notices.setdefault(n["id"], n)

    lst = sorted(notices.values(), key=lambda n: (n.get("time") or n.get("date") or "", int(n["id"])), reverse=True)
    out = {"source": "中華民國中醫師公會全國聯合會 https://twtm.tw/",
           "updated": datetime.now(TW).strftime("%Y-%m-%d %H:%M"),
           "categories": [n for _, _, n in SOURCES], "notices": lst}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (RAW / "last-run.txt").write_text(f"{out['updated']} 共 {len(lst)} 則，新增 {new_count} 則\n", encoding="utf-8")
    log.info("輸出 %s：共 %d 則，新增 %d 則", OUT, len(lst), new_count)
    if not lst:
        sys.exit("沒有抓到任何公告，可能網站改版")


if __name__ == "__main__":
    main()
