#!/usr/bin/env python3
"""附件重點：網頁內文很短、內容在附件（PDF／DOCX）的公告，下載附件轉文字後請 AI 摘要，寫回 docs/digest.json 的 attachmentDigest。
- 對象：digest 中非名單類、非年度計畫比對、內文 < 400 字且有 PDF/DOCX 附件者（例如研商議事會議紀錄、函轉公告、作業說明）
- 每點重點須附原文引句；程式核對引句確實出現在附件文字中並記下頁碼，對不上的丟掉；重點裡的數字也必須出現在原文
- 快取：docs/attachments.json（以附件網址集合為鍵）；原檔與文字：raw/attachments/；log：raw/attachments-log.txt
用法：python3 scripts/attachments.py [--limit N]
"""
import argparse, hashlib, io, json, logging, re, subprocess, urllib.request, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/notices.json"
DIGEST = ROOT / "docs/digest.json"
CACHE = ROOT / "docs/attachments.json"
WORK = ROOT / "raw/attachments"
LOG = ROOT / "raw/attachments-log.txt"
TW = timezone(timedelta(hours=8))
MODEL = "sonnet"
LIST_RE = re.compile(r"名單|名冊|名錄")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("attachments")

PROMPT = """以下是台灣中醫師公會全聯會一則公告的附件全文（可能是會議紀錄、函文、作業說明）。讀者是執業中醫師。
請整理「對中醫師有什麼影響」，重點放在：點值、支付標準與給付、申報規定、承作或收案資格、截止日期、需要配合辦理的事。
與中醫無關的議題、議程程序、出席名單不要寫。

輸出 JSON：
{{"summary":"一句話總結（40 字內）",
 "points":[{{"text":"重點（50 字內，白話）","quote":"附件原文中的一小段（15～60 字，逐字照抄，用來核對）"}}],
 "action":"需要醫師或院所做的事（沒有則 null）"}}
points 最多 6 點，依重要性排序。數字、日期要和原文一致。只輸出 JSON。

公告標題：{title}
附件全文（每頁以【第 N 頁】標示）：
{text}
"""


def fetch(url, path):
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tcm-pocket-feed"})
        data = urllib.request.urlopen(req, timeout=60).read()
        if len(data) > 20_000_000:
            raise ValueError("檔案過大")
        path.write_bytes(data)
    return path


def pages_of(path):
    """回傳每頁文字（DOCX 視為一頁）"""
    if path.suffix == ".pdf":
        out = subprocess.run(["pdftotext", str(path), "-"], capture_output=True, timeout=120).stdout.decode("utf-8", "ignore")
        return out.split("\f")
    if path.suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return [re.sub(r"<[^>]+>", "", xml)]
    return []


def flat(s):
    return re.sub(r"\s+", "", s).translate(str.maketrans("０１２３４５６７８９．，：；（）", "0123456789.,:;()"))


def call_claude(prompt):
    r = subprocess.run(["claude", "-p", "--model", MODEL, "--output-format", "json",
                        "--system-prompt", "你是精準的中文健保與醫事法規編輯，只輸出使用者要求的 JSON。",
                        "--tools", "", "--max-turns", "1", "--strict-mcp-config"],
                       input=prompt, capture_output=True, text=True, timeout=400)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p 失敗：{r.stderr[-300:]}")
    outer = json.loads(r.stdout)
    m = re.search(r"\{.*\}", outer.get("result", ""), re.S)
    if not m:
        raise ValueError("回應不是 JSON")
    return json.loads(m.group(0)), outer.get("total_cost_usd") or 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15, help="本次最多處理幾則新公告")
    args = ap.parse_args()
    notices = {n["id"]: n for n in json.loads(SRC.read_text(encoding="utf-8"))["notices"]}
    digest = json.loads(DIGEST.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    WORK.mkdir(parents=True, exist_ok=True)
    cost, done = 0.0, 0
    with LOG.open("a", encoding="utf-8") as logf:
        for item in sorted(digest["items"], key=lambda x: int(x["id"]), reverse=True):
            n = notices.get(item["id"])
            ai = item.get("ai") or {}
            if not n or ai.get("kind") == "list" or LIST_RE.search(n["title"]) or item.get("planDiff"):
                continue
            if len(n.get("body") or "") >= 400:
                continue
            atts = [a for a in (item.get("attachments") or []) if re.search(r"\.(pdf|docx)(\?|$)", a["url"].lower())][:3]
            if not atts:
                continue
            key = hashlib.sha1("|".join(a["url"] for a in atts).encode()).hexdigest()[:12]
            if key not in cache:
                if done >= args.limit:
                    continue
                pages, files = [], []
                try:
                    for i, a in enumerate(atts):
                        ext = ".pdf" if ".pdf" in a["url"].lower() else ".docx"
                        p = fetch(a["url"], WORK / f"{item['id']}-{i}{ext}")
                        ps = pages_of(p)
                        (WORK / f"{item['id']}-{i}.txt").write_text("\f".join(ps), encoding="utf-8")
                        files.append({"label": a.get("label"), "url": a["url"], "start": len(pages)})
                        pages += ps
                except Exception as e:
                    log.warning("[%s] 下載或轉文字失敗：%s", item["id"], e)
                    continue
                text = "\n".join(f"【第 {i + 1} 頁】\n{t}" for i, t in enumerate(pages))
                if len(flat(text)) < 80:
                    cache[key] = {"skip": "附件無文字（可能是掃描檔）"}
                    continue
                res = None
                for attempt in range(2):   # JSON 偶爾因引句含引號而壞掉，重試一次
                    try:
                        res, c = call_claude(PROMPT.format(title=n["title"], text=text[:60000]) + ("\n（注意：quote 內的雙引號請改用「」）" if attempt else ""))
                        break
                    except Exception as e:
                        log.warning("[%s] AI 失敗（第 %d 次）：%s", item["id"], attempt + 1, e)
                if res is None:
                    continue
                cost += c; done += 1
                fp = [flat(t) for t in pages]
                alltext = "".join(fp)
                kept, notes = [], []
                for pt in res.get("points") or []:
                    q = flat(pt.get("quote") or "")
                    page = next((i + 1 for i, t in enumerate(fp) if q and q in t), None)
                    nums_ok = all(x in alltext for x in re.findall(r"\d+(?:\.\d+)?", flat(pt.get("text") or "")))
                    if len(q) < 10 or page is None:
                        notes.append(f"丟棄（引句對不上）：{pt.get('text')}")
                        continue
                    if not nums_ok:
                        notes.append(f"丟棄（數字對不上）：{pt.get('text')}")
                        continue
                    f = max((x for x in files if x["start"] < page), key=lambda x: x["start"])
                    kept.append({"text": pt["text"], "quote": pt["quote"], "page": page - f["start"], "file": f["label"], "url": f["url"]})
                summ = res.get("summary")
                if summ and not all(x in alltext for x in re.findall(r"\d+(?:\.\d+)?", flat(summ))):
                    notes.append(f"總結數字對不上，改用第一點：{summ}")
                    summ = kept[0]["text"] if kept else None
                cache[key] = {"summary": summ, "points": kept, "action": res.get("action")}
                line = f"{datetime.now(TW):%F %T}\t{item['id']}\t{n['title'][:30]}\t{len(kept)} 點\t{'；'.join(notes)}"
                logf.write(line + "\n"); log.info(line)
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            r = cache.get(key) or {}
            if r.get("points"):
                item["attachmentDigest"] = r
    DIGEST.write_text(json.dumps(digest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("附件重點：本次 %d 則（約 US$%.3f）", done, cost)


if __name__ == "__main__":
    main()
