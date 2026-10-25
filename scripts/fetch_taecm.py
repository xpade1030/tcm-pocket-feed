#!/usr/bin/env python3
"""抓取台灣中醫醫學教育學會（taecm.com.tw）學會公告，併入 docs/notices.json（作者 2026-10-25：師培課程與認證辦法）。

- 列表：news/（單頁列出全部）；內頁：news/ins.php?index_id=<編號>&index_m_id=0
- 自己的快取 docs/taecm.json（fetch_twtm.py 每次重寫 notices.json，不能靠它記住已抓過的內頁）
- 編號加前綴 taecm-，避免與全聯會編號相撞；notices 加 source 欄位標示來源
- 只收近 WINDOW_DAYS 天發布的公告（更舊的多是已結束的課程，AI 整理了也會被當過期丟掉）
必須在 fetch_twtm.py 之後執行。
"""
import json, logging, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_twtm import get, text_of, TW  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / "docs/notices.json"
CACHE = ROOT / "docs/taecm.json"
RAW = ROOT / "raw"
BASE = "https://taecm.com.tw/news/"
SOURCE = "台灣中醫醫學教育學會"
WINDOW_DAYS = 180
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("taecm")

ITEM = re.compile(r'<li class="newsTxt">.*?newIcon[^>]*>(.*?)</div>.*?fa-clock-o"></i>\s*([0-9]{1,2})\.([A-Za-z]{3})\.([0-9]{4})'
                  r'.*?href="ins\.php\?index_id=(\d+)&(?:amp;)?index_m_id=0"\s+title="([^"]*)"', re.S)


def parse_list(page):
    out = []
    for cat, d, mon, y, nid, title in ITEM.findall(page):
        m = MONTHS.get(mon.lower())
        if m:
            out.append({"id": nid, "category": text_of(cat), "date": f"{y}-{m:02d}-{int(d):02d}", "title": text_of(title)})
    return out


def parse_detail(page):
    start = page.find('<div class="textEditor')
    end = page.find('<div class="side_share"', start)
    body_html = page[start:end] if start >= 0 and end > start else ""
    atts = []
    for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body_html, re.S):
        href = href.replace("&amp;", "&")
        if href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = href if href.startswith("http") else "https://taecm.com.tw/" + href.lstrip("./")
        atts.append({"label": text_of(label) or url, "url": url})
    return text_of(body_html)[:6000], atts[:30]


def selfcheck():
    sample = ('<li class="newsTxt"><div class="newsDate"><div class="newIcon news_orange">師培作業公告</div>'
              '<strong><i class="fa fa-clock-o"></i>21.Oct.2026</strong></div>'
              '<h3><a  href="ins.php?index_id=411&index_m_id=0"  title="115年度 中醫OSCE考官 初次認證 申請辦法">x</a></h3></li>')
    assert parse_list(sample) == [{"id": "411", "category": "師培作業公告", "date": "2026-10-21", "title": "115年度 中醫OSCE考官 初次認證 申請辦法"}], parse_list(sample)


def main():
    selfcheck()
    RAW.mkdir(exist_ok=True)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    page = get(BASE)
    listed = parse_list(page)
    log.info("列表 %d 則", len(listed))
    if not listed:
        (RAW / "taecm-list.html").write_text(page, encoding="utf-8")
        sys.exit("醫學教育學會列表抓不到任何公告，可能網站改版（列表已存 raw/taecm-list.html）")
    since = (datetime.now(TW) - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    keep, new = {}, 0
    for it in listed:
        if it["date"] < since:
            continue
        nid = f"taecm-{it['id']}"
        n = cache.get(nid)
        if not n or n.get("title") != it["title"]:
            url = f"{BASE}ins.php?index_id={it['id']}&index_m_id=0"
            body, atts = parse_detail(get(url))
            n = {"id": nid, "source": SOURCE, "category": f"醫學教育學會：{it['category']}", "section": SOURCE,
                 "title": it["title"], "date": it["date"], "time": None, "body": body, "attachments": atts, "url": url}
            new += 1
            log.info("  新增 %s %s", n["date"], n["title"][:40])
            time.sleep(0.6)
        keep[nid] = n
    CACHE.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")

    data = json.loads(NOTICES.read_text(encoding="utf-8"))
    data["notices"] = [n for n in data["notices"] if not str(n["id"]).startswith("taecm-")] + list(keep.values())
    data["notices"].sort(key=lambda n: n.get("time") or n.get("date") or "", reverse=True)
    if SOURCE not in data.get("source", ""):
        data["source"] += f"；{SOURCE} {BASE}"
    NOTICES.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    msg = f"{datetime.now(TW):%F %H:%M} 醫學教育學會 近 {WINDOW_DAYS} 天 {len(keep)} 則，新增 {new} 則"
    with (RAW / "last-run.txt").open("a", encoding="utf-8") as f:
        f.write(msg + "\n")
    log.info(msg)


if __name__ == "__main__":
    main()
