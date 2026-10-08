#!/usr/bin/env python3
"""年度計畫新版比對：下載新舊兩版公告附件（PDF），轉文字後用 difflib 找出差異段落，
再請 AI 用白話列出「這次改了什麼」。輸出 docs/plan-diffs.json（以新版公告編號為鍵）。

全聯會的「(115年度公告版)」公告幾乎不寫改了哪裡；這支程式就是要回答這個問題。
- 先由程式算出逐行差異（只把差異餵給 AI，省額度也避免 AI 自己編）
- AI 每點變更須附「新版原文片段」，程式檢查片段確實出現在新版全文，對不上的丟掉
- raw/plan-diff/ 保存下載的 PDF、轉出的文字與差異檔，方便人工核對
用法：python3 scripts/plan_diff.py --new 4004 --old 3754
"""
import argparse, difflib, json, logging, re, subprocess, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/notices.json"
OUT = ROOT / "docs/plan-diffs.json"
WORK = ROOT / "raw/plan-diff"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("plan_diff")

PROMPT = """以下是台灣健保「{name}」新舊兩個年度公告版之間的逐行差異（- 為舊版刪除、+ 為新版新增）。
請用白話向中醫師說明「這次改了什麼」，重點放在會影響申報、收案條件、給付點數、資格、量表、期限的變更；排版、頁碼、日期戳記、文字微調不用列。

輸出 JSON：{{"summary":"一句話總結（40 字內）","changes":[{{"topic":"變更主題（10 字內）","before":"舊版重點（沒有則 null）","after":"新版重點","quote":"新版原文中的一小段（15～40 字，逐字照抄，用於核對）","impact":"對醫師的實際影響（一句話）"}}]}}
最多 10 點，依重要性排序。只輸出 JSON。

差異：
{diff}
"""


def download(url, path):
    if not path.exists():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 tcm-pocket-feed"})
        path.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    return path


def to_text(pdf):
    txt = pdf.with_suffix(".txt")
    subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)
    return txt.read_text(encoding="utf-8", errors="ignore")


def norm_lines(t):
    out = []
    for l in t.splitlines():
        l = re.sub(r"\s+", "", l)
        if not l or re.fullmatch(r"[-－—\d第頁共/ ]+", l):
            continue
        out.append(l)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", required=True)
    args = ap.parse_args()
    notices = {n["id"]: n for n in json.loads(SRC.read_text(encoding="utf-8"))["notices"]}
    new, old = notices[args.new], notices[args.old]
    WORK.mkdir(parents=True, exist_ok=True)
    texts = {}
    for tag, n in (("new", new), ("old", old)):
        pdf_url = next(a["url"] for a in n["attachments"] if a["url"].lower().endswith(".pdf"))
        pdf = download(pdf_url, WORK / f"{n['id']}.pdf")
        texts[tag] = to_text(pdf)
        log.info("%s 版：%s（%d 字）", tag, n["title"][:40], len(texts[tag]))
    diff = list(difflib.unified_diff(norm_lines(texts["old"]), norm_lines(texts["new"]), lineterm="", n=1))
    diff_text = "\n".join(l for l in diff if not l.startswith(("---", "+++")))
    (WORK / f"{args.new}-vs-{args.old}.diff").write_text(diff_text, encoding="utf-8")
    log.info("差異 %d 行", diff_text.count("\n"))
    name = re.sub(r"^檢附「|」.*$", "", new["title"])
    prompt = PROMPT.format(name=name, diff=diff_text[:60000])
    r = subprocess.run(["claude", "-p", "--model", "sonnet", "--output-format", "json",
                        "--system-prompt", "你是精準的中文健保法規編輯，只輸出使用者要求的 JSON。",
                        "--tools", "", "--max-turns", "1", "--strict-mcp-config"],
                       input=prompt, capture_output=True, text=True, timeout=600)
    outer = json.loads(r.stdout)
    res = json.loads(re.search(r"\{.*\}", outer["result"], re.S).group(0))
    newflat = re.sub(r"\s+", "", texts["new"])
    kept = []
    for c in res.get("changes", []):
        q = re.sub(r"\s+", "", c.get("quote") or "")
        if len(q) >= 10 and q in newflat:   # 太短的片段（如「附件二」）無法佐證，不採用
            kept.append(c)
        else:
            log.warning("丟棄（原文對不上）：%s｜%s", c.get("topic"), c.get("quote"))
    res["changes"] = kept
    res.update({"newId": args.new, "oldId": args.old, "newTitle": new["title"], "oldTitle": old["title"],
                "newUrl": new["url"], "oldUrl": old["url"]})
    allres = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    allres[args.new] = res
    OUT.write_text(json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("完成：%d 點變更（花費約 US$%.3f）", len(kept), outer.get("total_cost_usd") or 0)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
