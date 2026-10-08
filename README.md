# tcm-pocket-feed

「中醫臨床指南針」App 的公開資料源。GitHub Actions 每天兩次（台灣時間 06:00、13:00）抓取中醫師公會全國聯合會（https://twtm.tw/）公開公告，輸出 `docs/notices.json`，經 GitHub Pages 發佈：

https://xpade1030.github.io/tcm-pocket-feed/notices.json

- 抓取程式：`scripts/fetch_twtm.py`（只用 Python 標準函式庫；增量更新，已抓過的內頁不重抓；`--full` 全部重抓）
- 收錄分類：大眾最新消息、新聞稿、法規專區、會務快訊、中醫點值結算說明、下載專區（每類最近約 50 則）
- `raw/`：最近一次執行的 log

內容版權屬原發布單位，本資料源僅整理標題、日期、內文與原文連結，供 App 顯示並導向原網頁。
