#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_data.py
================
高雄機場出境電子看板 - 後端資料更新程式

目錄結構（部署到 GitHub Pages 時的慣例）：
    repo 根目錄/
      ├─ scripts/update_data.py      ← 本檔案
      ├─ .cache/khfids/              ← 資料檔存放處
      │    ├─ iata_icao_override.json  （手動維護，需自行先建立/提交）
      │    ├─ data.js                  （本程式產生）
      │    └─ update.log               （本程式產生）
      └─ docs/                       ← GitHub Pages 發布目錄
           ├─ dashboard.html
           └─ data.js                （本程式會自動從 .cache/khfids/ 複製一份過來）

功能：
  1. 抓取 https://ccc.kia.gov.tw/fids/json/web/dep.php，取出 FDATE 為「今天」的資料
  2. 抓取 https://www.kia.gov.tw/data/airline2.json，將 airLineNum 前2碼(IATA航空公司代碼)
     替換為對應的3碼 ICAO 代碼
  3. 抓取 https://www.kia.gov.tw/data/airport2.json，將 ArrivalAirportIATA(3碼)
     替換為對應的4碼 ICAO 機場代碼
  4. 若 .cache/khfids/iata_icao_override.json 手動對照表中有該3碼機場代碼，
     優先使用手動對照表的結果，找不到才使用 airport2.json 的結果
  5. 將整理好的結果寫入 .cache/khfids/data.js，並自動複製一份到 docs/data.js
     （供 GitHub Pages 上的 dashboard.html 讀取顯示）

使用方式：
  單次執行（適合搭配 cron / 排程器 / GitHub Actions）：
      python3 scripts/update_data.py

  持續迴圈執行（適合直接背景常駐，例如搭配 systemd 或 nohup；不建議用於 GitHub Actions）：
      python3 scripts/update_data.py --loop 30      # 每 30 秒更新一次

注意：
  - 寫檔採「先寫暫存檔，再原子性 rename」的方式，避免前端讀到寫一半的檔案。
  - 若抓取失敗，會保留舊的 data.js 不動，並把錯誤訊息寫進 update.log，方便排查。
"""

import json
import sys
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ------------------------- 基本設定 -------------------------

DEP_URL = "https://ccc.kia.gov.tw/fids/json/web/dep.php"
AIRLINE_URL = "https://www.kia.gov.tw/data/airline2.json"
AIRPORT_URL = "https://www.kia.gov.tw/data/airport2.json"

# 本檔案放在 repo 的 scripts/ 目錄下，往上一層就是 repo 根目錄
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# 資料檔（含手動對照表、log）統一放在 .cache/khfids/ 目錄
CACHE_DIR = REPO_ROOT / ".cache" / "khfids"
CACHE_DIR.mkdir(parents=True, exist_ok=True)  # 確保目錄存在（git 不會追蹤空目錄）

OVERRIDE_FILE = CACHE_DIR / "iata_icao_override.json"
# 輸出成 .js（而非 .json）：內容是 window.FIDS_DATA = {...}; 這種可執行的 JS 賦值語法，
# 讓 dashboard.html 用 <script src="data.js"> 載入，不受瀏覽器對 fetch()/XHR 讀取本機檔案的限制。
OUTPUT_FILE = CACHE_DIR / "data.js"
OUTPUT_TMP = CACHE_DIR / "data.js.tmp"
LOG_FILE = CACHE_DIR / "update.log"

# GitHub Pages 實際發布的目錄（Pages 設定為 "/docs" 時，只有這個目錄底下的檔案會被發布）。
# dashboard.html 放在這裡，所以每次更新完 .cache/khfids/data.js 後，
# 還要再複製一份到 docs/data.js，網頁才讀得到最新資料。
DOCS_DIR = REPO_ROOT / "docs"
DOCS_DATA_FILE = DOCS_DIR / "data.js"

TW_TZ = timezone(timedelta(hours=8))  # 台灣時間 UTC+8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KIA-FIDS-Dashboard/1.0)",
    "Accept": "application/json,text/plain,*/*",
}


REQUEST_TIMEOUT = 15   # 秒
MAX_RETRIES = 3        # 5xx / 連線失敗時最多重試次數（不含第一次嘗試）
RETRY_BACKOFF_SECONDS = 5  # 每次重試間隔（秒），採固定間隔，第N次重試等待 N * 此秒數


# ------------------------- 共用工具函式 -------------------------

def log(msg: str):
    line = f"[{datetime.now(TW_TZ).isoformat()}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def fetch_json(url: str):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 2):  # 第一次嘗試 + 最多 MAX_RETRIES 次重試
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
            break  # 成功就跳出重試迴圈

        except urllib.error.HTTPError as e:
            body_snippet = ""
            try:
                body_snippet = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_error = RuntimeError(
                f"HTTP {e.code} {e.reason} - URL: {url}"
                + (f" - 回應內容前500字: {body_snippet}" if body_snippet else "（回應內容為空）")
            )
            # 4xx 通常代表持續性拒絕（例如被擋、網址錯誤），重試也不會變好，直接放棄
            if 400 <= e.code < 500:
                raise last_error from e
            # 5xx 視為可能的暫時性錯誤，值得重試
            if attempt <= MAX_RETRIES:
                log(f"第 {attempt} 次嘗試失敗（{e.code}），{RETRY_BACKOFF_SECONDS * attempt} 秒後重試 - {url}")
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise last_error from e

        except urllib.error.URLError as e:
            last_error = RuntimeError(f"連線失敗 - URL: {url} - 原因: {e.reason}")
            if attempt <= MAX_RETRIES:
                log(f"第 {attempt} 次嘗試連線失敗，{RETRY_BACKOFF_SECONDS * attempt} 秒後重試 - {url}")
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise last_error from e

    # 有些政府網站的 JSON 檔含 BOM 或非標準編碼，這裡做寬鬆處理
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"JSON 解析失敗 - URL: {url} - 回應內容前500字: {text[:500]}"
        ) from e


def extract_list(payload):
    """
    保守處理回應格式：
    有些 API 直接回傳陣列 [...]；有些會包一層物件 {"data": [...]} 之類。
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "Data", "DATA", "list", "List", "rows", "Rows", "result", "Result"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        for v in payload.values():
            if isinstance(v, list):
                return v
    raise ValueError("無法從回應內容中找到資料陣列，來源 JSON 結構可能已變更")


def normalize_date_digits(s) -> str:
    """把日期字串中的非數字字元去掉，例如 '2026-07-04' 或 '2026/07/04' -> '20260704'"""
    if s is None:
        return ""
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return digits[:8]


# ------------------------- 對照表建立 -------------------------

def build_airline_map(airline_list):
    """AirlineIATA(2碼) -> AirlineICAO(3碼)"""
    m = {}
    for item in airline_list:
        iata = str(item.get("AirlineIATA", "")).strip().upper()
        icao = str(item.get("AirlineICAO", "")).strip().upper()
        if iata and icao:
            m[iata] = icao
    return m


def build_airport_map(airport_list):
    """IATA(3碼) -> ICAO(4碼)，來自 airport2.json"""
    m = {}
    for item in airport_list:
        iata = str(item.get("IATA", "")).strip().upper()
        icao = str(item.get("ICAO", "")).strip().upper()
        if iata and icao:
            m[iata] = icao
    return m


def load_override_map():
    """讀取手動維護的 IATA -> ICAO 對照表，格式為簡單的 { "IATA3碼": "ICAO4碼", ... }"""
    if OVERRIDE_FILE.exists():
        with open(OVERRIDE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k).strip().upper(): str(v).strip().upper() for k, v in data.items()}
    return {}


# ------------------------- 代碼轉換邏輯 -------------------------

def convert_airline_num(air_line_num, airline_map: dict) -> str:
    """
    把 airLineNum 前2碼(IATA航空代碼) 換成 airline2.json 對應的3碼 ICAO 代碼。
    例如 CI0012 -> CAL0012（若 CI 對應到 CAL）。
    找不到對照時，原樣保留。
    """
    if not air_line_num:
        return air_line_num
    s = str(air_line_num).strip()
    prefix2 = s[:2].upper()
    rest = s[2:]
    icao3 = airline_map.get(prefix2)
    if icao3:
        return icao3 + rest
    return s


def convert_airport_code(iata3, override_map: dict, airport_map: dict) -> str:
    """
    把 ArrivalAirportIATA(3碼) 換成4碼 ICAO 代碼。
    優先查手動對照表 override_map，找不到才查 airport2.json 的 airport_map。
    兩邊都找不到則原樣保留3碼。
    """
    if not iata3:
        return iata3
    code = str(iata3).strip().upper()
    if code in override_map:
        return override_map[code]
    if code in airport_map:
        return airport_map[code]
    return code


# ------------------------- 主要流程 -------------------------

def build_dashboard_data():
    dep_payload = fetch_json(DEP_URL)
    airline_payload = fetch_json(AIRLINE_URL)
    airport_payload = fetch_json(AIRPORT_URL)

    dep_list = extract_list(dep_payload)
    airline_list = extract_list(airline_payload)
    airport_list = extract_list(airport_payload)

    airline_map = build_airline_map(airline_list)
    airport_map = build_airport_map(airport_list)
    override_map = load_override_map()

    today_digits = datetime.now(TW_TZ).strftime("%Y%m%d")

    rows = []
    for item in dep_list:
        if normalize_date_digits(item.get("FDATE")) != today_digits:
            continue

        rows.append({
            "airLineNum": convert_airline_num(item.get("airLineNum", ""), airline_map),
            "airLineNum_orig": item.get("airLineNum", ""),
            "ArrivalAirport": convert_airport_code(
                item.get("ArrivalAirportIATA", ""), override_map, airport_map
            ),
            "ArrivalAirport_orig": item.get("ArrivalAirportIATA", ""),
            "STD": item.get("STD", ""),
            "statusdep": item.get("statusdep", ""),
            "notedep": item.get("notedep", ""),
            "Bay": item.get("Bay", ""),
        })

    # 依表定時間排序（HH:MM 格式的字串排序等同時間排序）
    rows.sort(key=lambda r: str(r.get("STD", "")))

    return {
        "updated_at": datetime.now(TW_TZ).isoformat(),
        "flight_date": today_digits,
        "count": len(rows),
        "flights": rows,
    }


def write_output(data: dict):
    js_text = "window.FIDS_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n"

    # 先寫進 .cache/khfids/data.js（原子性取代，避免讀到寫一半的檔案）
    with open(OUTPUT_TMP, "w", encoding="utf-8") as f:
        f.write(js_text)
    OUTPUT_TMP.replace(OUTPUT_FILE)

    # 若 docs/ 目錄存在（GitHub Pages 發布用的目錄），同步複製一份過去，
    # 讓部署到 GitHub Pages 的 dashboard.html 也能讀到最新資料。
    # 本機單獨執行、還沒有 docs/ 目錄的情況下會自動略過，不會報錯。
    if DOCS_DIR.exists():
        docs_tmp = DOCS_DIR / "data.js.tmp"
        with open(docs_tmp, "w", encoding="utf-8") as f:
            f.write(js_text)
        docs_tmp.replace(DOCS_DATA_FILE)


def run_once() -> bool:
    try:
        data = build_dashboard_data()
        write_output(data)
        log(f"更新成功，共 {data['count']} 筆航班")
        return True
    except (RuntimeError, ValueError) as e:
        log(f"更新失敗：{e}")
        return False
    except Exception as e:  # 保底，避免排程任務因未預期例外而整個中斷
        log(f"未預期錯誤：{e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="高雄機場出境電子看板 - 資料更新程式")
    parser.add_argument(
        "--loop", type=int, default=0,
        help="以迴圈方式每 N 秒更新一次；不指定則只執行一次（適合搭配 cron/排程器）"
    )
    args = parser.parse_args()

    if args.loop and args.loop > 0:
        log(f"以迴圈模式啟動，每 {args.loop} 秒更新一次（Ctrl+C 結束）")
        while True:
            run_once()  # 迴圈模式下單次失敗不中斷程式，下一輪繼續重試
            time.sleep(args.loop)
    else:
        # 單次執行模式（cron / GitHub Actions 用）：失敗時要回傳非 0 結束碼，
        # 這樣排程器 / CI 才能偵測到這次更新失敗（例如讓 GitHub Actions 該次執行顯示紅色，
        # 並觸發後續設定的失敗通知或診斷步驟）。
        success = run_once()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
