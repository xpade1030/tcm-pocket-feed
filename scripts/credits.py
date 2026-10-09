#!/usr/bin/env python3
"""課程學時／積分結構化：把 digest 中每堂課的「可取得哪些學時」拆成 制度 → 種類 → 數量，寫回 docs/digest.json。
制度（system）：
  pgy      負責醫師基本訓練學時（含「PGY 學時」）：醫務行政、衛生政策、實證醫學、醫學倫理/醫學法規、感染控制、醫療品質…
  ce       繼續教育積分：專業、品質、倫理、法規、感控、性別
  program  專案資格學時：感染控制計畫課程、針灸標準作業程序課程、三高、居家醫療…
  mentor   師培時數：指導師資初次認證、展延…
防幻覺：數量必須出現在原文（含全形數字）；對不上的數量改為 null 並記錄。原文只說「可申請／詳見報名表」者 amount=null。
快取：docs/credits.json（以「編號＋內文雜湊」為鍵）；log：raw/credits-log.txt
用法：python3 scripts/credits.py（在 enrich.py 之後執行）
"""
import hashlib, json, logging, re, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/notices.json"
DIGEST = ROOT / "docs/digest.json"
CACHE = ROOT / "docs/credits.json"
LOG = ROOT / "raw/credits-log.txt"
TW = timezone(timedelta(hours=8))
MODEL = "sonnet"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("credits")

SYSTEMS = {"pgy", "ce", "program", "mentor"}
PROMPT = """以下是台灣中醫師公會全聯會的課程公告原文。請找出「參加這堂課可以取得哪些學時或積分」，拆成結構化清單。

制度（system）只能是：
- "pgy"：負責醫師基本訓練課程學時（原文寫「PGY 學時」也算這個）。type 用：醫務行政、衛生政策、實證醫學、醫學倫理/醫學法規、感染控制、醫療品質，原文另有其他種類就照原文。
- "ce"：繼續教育積分（點數）。type 用：專業、品質、倫理、法規、感控、性別。
- "program"：專案或計畫的受訓資格學時，例如「感染控制計畫課程」「針灸標準作業程序課程」「三高照護計畫」「居家醫療」。type 照原文的課程名稱寫。
- "mentor"：指導師資（師培）認證或展延時數。type 照原文寫。

規則：
- amount 是數字（可為小數），必須是原文寫出的數字；原文只寫「可申請」「詳見報名表」而沒有數字時 amount 填 null。
- 原文有總數也有細項時，只列細項（例如「繼續教育 8 點：感控 3、品質 3、性別 1、專業 1」→ 四筆）；只有總數時，type 填 "總計"。
- 原文明說「無繼續教育學分」「不提供積分」的制度不要列。
- unit 填原文用字：點、學時、小時。
- 若各分區（台北區、北區、中區、南區、高屏區、東區）給的學時不同，每筆加 "region":"北區" 這類欄位；全部分區相同或沒分區就不要加 region。
- 沒有任何學時資訊就回傳空陣列。

輸出 JSON：{{"items":[{{"system":"pgy","type":"感染控制","amount":3,"unit":"學時"}}],"note":"一句話補充（例如：依各區課程而異），沒有則 null"}}
只輸出 JSON。

標題：{title}
原文：
{body}
"""


def body_hash(n):
    return hashlib.sha1((n["title"] + (n.get("body") or "")).encode()).hexdigest()[:12]


def call_claude(prompt):
    r = subprocess.run(["claude", "-p", "--model", MODEL, "--output-format", "json",
                        "--system-prompt", "你是精準的資料擷取員，只輸出使用者要求的 JSON。",
                        "--tools", "", "--max-turns", "1", "--strict-mcp-config"],
                       input=prompt, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p 失敗：{r.stderr[-300:]}")
    outer = json.loads(r.stdout)
    m = re.search(r"\{.*\}", outer.get("result", ""), re.S)
    if not m:
        raise ValueError("回應不是 JSON")
    return json.loads(m.group(0)), outer.get("total_cost_usd") or 0


def to_half(s):
    return s.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))


def verify(res, text):
    """數量必須出現在原文；system 必須合法"""
    flat = to_half(re.sub(r"\s+", "", text))
    kept, notes = [], []
    for it in res.get("items") or []:
        if it.get("system") not in SYSTEMS or not it.get("type"):
            notes.append(f"丟棄不合法：{it}")
            continue
        a = it.get("amount")
        if a is not None:
            s = ("%g" % float(a))
            if not re.search(rf"(?<![\d.]){re.escape(s)}(?![\d.])", flat):
                notes.append(f"數量對不上原文，改為 null：{it}")
                it["amount"] = None
        kept.append({"system": it["system"], "type": it["type"].strip(), "amount": it.get("amount"), "unit": it.get("unit"),
                     **({"region": it["region"]} if it.get("region") in ("台北區", "北區", "中區", "南區", "高屏區", "東區") else {})})
    # 同一制度已有細項時，拿掉沒有數字的「總計」
    detailed = {(k["system"], k.get("region")) for k in kept if k["type"] != "總計"}
    kept = [k for k in kept if not (k["type"] == "總計" and k["amount"] is None and (k["system"], k.get("region")) in detailed)]
    return {"items": kept, "note": res.get("note")}, notes


def main():
    notices = {n["id"]: n for n in json.loads(SRC.read_text(encoding="utf-8"))["notices"]}
    digest = json.loads(DIGEST.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    LOG.parent.mkdir(exist_ok=True)
    cost, done = 0.0, 0
    with LOG.open("a", encoding="utf-8") as logf:
        for item in digest["items"]:
            ai = item.get("ai") or {}
            if not ai.get("course"):
                continue
            n = notices.get(item["id"])
            if not n:
                continue
            key = f"{n['id']}:{body_hash(n)}"
            if key not in cache:
                try:
                    res, c = call_claude(PROMPT.format(title=n["title"], body=(n.get("body") or "")[:8000]))
                except Exception as e:
                    log.warning("[%s] 失敗：%s", n["id"], e)
                    continue
                res, notes = verify(res, n["title"] + (n.get("body") or ""))
                cache[key] = res
                cost += c; done += 1
                line = f"{datetime.now(TW):%F %T}\t{n['id']}\t{n['title'][:30]}\t{json.dumps(res, ensure_ascii=False)}\t{'；'.join(notes)}"
                logf.write(line + "\n"); log.info(line)
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            r = cache[key]
            detailed = {(k["system"], k.get("region")) for k in r["items"] if k["type"] != "總計"}
            r["items"] = [k for k in r["items"] if not (k["type"] == "總計" and k["amount"] is None and (k["system"], k.get("region")) in detailed)]
            ai["course"]["creditItems"] = r["items"]
            if r.get("note"):
                ai["course"]["creditNote"] = r["note"]
    DIGEST.write_text(json.dumps(digest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log.info("學時結構化：本次 %d 則（約 US$%.3f）", done, cost)


if __name__ == "__main__":
    main()
