#!/usr/bin/env python3
"""AI 整理全聯會公告：篩選分級、改寫標題、濃縮重點、抽出結構化欄位，輸出 docs/digest.json。

- 輸入：docs/notices.json（fetch_twtm.py 產生的原文）
- 快取：docs/enriched.json（以「編號＋內文雜湊」為鍵；原文沒變就不重跑）
- AI 後端：`claude -p`（預設，使用 CLAUDE_CODE_OAUTH_TOKEN 或本機登入）；之後可換 API
- 防幻覺：日期、金額、網址必須能在原文（標題＋內文＋附件）中找到，否則清空並記錄
- raw/enrich-log.txt：每則的判定與被清掉的欄位
"""
import argparse, hashlib, json, logging, re, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/notices.json"
CACHE = ROOT / "docs/enriched.json"
OUT = ROOT / "docs/digest.json"
LOG = ROOT / "raw/enrich-log.txt"
TW = timezone(timedelta(hours=8))
MODEL = "haiku"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("enrich")

PROMPT = """你是台灣中醫臨床的編輯，讀者是忙碌的醫院與診所中醫師。請把下面這則「中華民國中醫師公會全國聯合會」公告整理成 JSON。

規則：
1. topics：依內容客觀歸類（不要判斷重要性），可複選 1～2 個，只能用以下代號：
   - "course"：課程與認證（研討會、繼續教育、師資培訓、專案課程、認證）
   - "nhi"：健保給付與點值（支付標準、點值結算、審查規定、健保計畫公告）
   - "law"：法規與主管機關（法令修正、衛福部／中醫藥司的要求或函釋）
   - "apply"：申請與截止事項（需要中醫師本人辦理的申請、換證、填報、繳費）
   - "policy"：政策與新聞（中醫政策動態、新聞稿）
   - "affairs"：會務與活動（公會活動、選舉、表揚、公益）
2. hidden：與一般中醫師無關或沒有閱讀價值者填 true（內部會議紀錄、會員代表登記冊、人事、轉知給雇主的勞動法規等），否則 false。
3. title：重寫成一行（30 字內）讓人一看就懂的標題，去掉【】與公文套話。
4. points：2～4 點重點，每點一句白話（40 字內），只寫原文有的內容。
5. 以下欄位原文沒有就填 null，不要推測；日期一律 YYYY-MM-DD（民國年請換算西元）：
   audience（適用對象）、action（讀者要做什麼，一句話）、deadline（截止日）、effective（生效日）
6. 若屬於課程（topics 含 course），填 course 物件：name、organizer、start、end（日期）、time（時段文字）、location、online（true/false/null）、credits（積分類別與點數，原文寫法）、fee、registerUrl（原文中的報名網址）、registerDeadline；否則 course 為 null。
7. 只輸出 JSON，不要任何說明文字。格式：
{"topics":[],"hidden":false,"title":"","points":[],"audience":null,"action":null,"deadline":null,"effective":null,"course":null}

公告分類：{category}
發布日期：{date}
原標題：{title}
內文：
{body}
附件與連結：
{links}
"""


def body_hash(n):
    return hashlib.sha1((n["title"] + (n.get("body") or "") + json.dumps(n.get("attachments") or [], ensure_ascii=False)).encode()).hexdigest()[:12]


def call_claude(prompt):
    # 精簡模式：換掉 Claude Code 冗長的預設系統提示、不載入工具與設定，單次回答（成本約降為 1/5）
    r = subprocess.run(["claude", "-p", "--model", MODEL, "--output-format", "json",
                        "--system-prompt", "你是精準的中文編輯，只輸出使用者要求的 JSON。",
                        "--tools", "", "--max-turns", "1", "--strict-mcp-config"],
                       input=prompt, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p 失敗：{r.stderr[-300:]}")
    outer = json.loads(r.stdout)
    text = outer.get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"回應不是 JSON：{text[:200]}")
    return json.loads(m.group(0)), outer.get("total_cost_usd")


def roc_variants(iso):
    """2026-10-31 → 可能出現在原文的寫法（西元、民國、月日）"""
    y, mo, d = iso.split("-")
    roc = int(y) - 1911
    m, dd = int(mo), int(d)
    return [iso, f"{y}/{m}/{dd}", f"{y}/{mo}/{d}", f"{roc}/{m}/{dd}", f"{roc}/{mo}/{d}", f"{roc}.{m}.{dd}",
            f"{roc}年{m}月{dd}日", f"{y}年{m}月{dd}日", f"{m}/{dd}", f"{m}月{dd}日", f"{mo}/{d}",
            f"{roc}{mo}{d}", f"{roc}.{mo}.{d}", f"{y}{mo}{d}"]   # 公文常見 1140801、114.08.01


def verify(d, source_text, notes):
    """日期、網址、金額要在原文找得到；找不到就清空（記錄在 notes）"""
    src = re.sub(r"\s+", "", source_text)

    def ok_date(v):
        return v is None or any(x.replace(" ", "") in src for x in roc_variants(v)) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v or "")) else v is None

    def check(obj, key, kind):
        v = obj.get(key)
        if v in (None, ""):
            obj[key] = None
            return
        good = ok_date(v) if kind == "date" else (str(v) in source_text) if kind == "url" else True
        if not good:
            notes.append(f"清除 {key}={v}")
            obj[key] = None

    for k in ("deadline", "effective"):
        check(d, k, "date")
    c = d.get("course")
    if isinstance(c, dict):
        for k in ("start", "end", "registerDeadline"):
            check(c, k, "date")
        check(c, "registerUrl", "url")
        fee = c.get("fee")
        if fee and re.search(r"\d", str(fee)) and not any(num in src for num in re.findall(r"\d[\d,]*", str(fee))):
            notes.append(f"清除 fee={fee}")
            c["fee"] = None
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="最多處理幾則新公告（0＝不限）")
    ap.add_argument("--ids", default="", help="只處理指定編號（逗號分隔，測試用）")
    args = ap.parse_args()
    notices = json.loads(SRC.read_text(encoding="utf-8"))["notices"]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    want = set(filter(None, args.ids.split(",")))
    LOG.parent.mkdir(exist_ok=True)
    logf = LOG.open("a", encoding="utf-8")
    done, cost = 0, 0.0
    for n in notices:
        if want and n["id"] not in want:
            continue
        key = f"{n['id']}:{body_hash(n)}"
        if key in cache:
            continue
        if args.limit and done >= args.limit:
            break
        links = "\n".join(f"- {a['label']}：{a['url']}" for a in (n.get("attachments") or [])) or "（無）"
        prompt = PROMPT.replace("{category}", n["category"]).replace("{date}", n.get("date") or "") \
            .replace("{title}", n["title"]).replace("{body}", (n.get("body") or "（無內文，只有附件）")[:5000]).replace("{links}", links)
        try:
            d, c = call_claude(prompt)
        except Exception as e:
            log.warning("[%s] 失敗：%s", n["id"], e)
            continue
        notes = []
        d = verify(d, n["title"] + "\n" + (n.get("body") or "") + "\n" + links, notes)
        cache[key] = d
        done += 1
        cost += c or 0
        tag = ",".join(d.get("topics") or []) + ("（隱藏）" if d.get("hidden") else "")
        log.info("[%s] %s｜%s %s", n["id"], tag, d.get("title"), "；".join(notes))
        logf.write(f"{datetime.now(TW):%F %T}\t{n['id']}\t{tag}\t{d.get('title')}\t{'；'.join(notes)}\n")
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(0.5)

    # 組 digest：原文欄位＋AI 整理（沒整理過的先標 pending，App 照原標題顯示）
    out = []
    for n in notices:
        e = cache.get(f"{n['id']}:{body_hash(n)}")
        out.append({"id": n["id"], "category": n["category"], "date": n.get("date"), "url": n["url"],
                    "origTitle": n["title"], "attachments": n.get("attachments") or [],
                    "ai": e, "status": "ok" if e else "pending"})
    OUT.write_text(json.dumps({"updated": datetime.now(TW).strftime("%Y-%m-%d %H:%M"), "items": out},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("本次整理 %d 則，估計花費 US$%.4f；digest 共 %d 則（待整理 %d）", done, cost, len(out),
             sum(1 for x in out if x["status"] == "pending"))


if __name__ == "__main__":
    main()
