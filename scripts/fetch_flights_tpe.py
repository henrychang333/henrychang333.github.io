#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/fetch_flights_tpe.py

從桃園機場動態航班 CSV 抓取資料一次，比對機場/航空公司代碼、換算 UTC 時間，
輸出 docs/flights_data_tpe.js 給 docs/dashboard_TPE.html 讀取顯示。

注意：這支程式「只抓取一次就結束」，不會自己排程重複執行。
更新頻率改由 GitHub Actions 的排程(cron)控制（見 .github/workflows/fetch-flights.yml）。

執行方式：
    python scripts/fetch_flights_tpe.py

需要的套件：
    pip install requests
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ------------------------- 設定 -------------------------
CSV_URL = "https://www.taoyuan-airport.com/uploads/flightx/a_flight_v4.txt"

# CSV 來源的文字編碼。已實測確認來源為 Big5，若之後來源改版導致中文亂碼，
# 可以改成 "utf-8" 或其他編碼試試。
CSV_CHARSET = "big5"

REPO_ROOT = Path(__file__).resolve().parent.parent
AIRPORT_XML = REPO_ROOT / "data" / "airport.xml"
AIRLINE_XML = REPO_ROOT / "data" / "airline.xml"
OUTPUT_JS = REPO_ROOT / "docs" / "flights_data_tpe.js"

TZ_TAIPEI = timezone(timedelta(hours=8))  # 台灣時間 = UTC+8（全年固定，無日光節約時間）

# column.txt 欄位1、欄位2 的代碼對照
# 航廈欄位：來源實際內容是「第一航廈／第二航廈／第三航廈」這種完整文字，
# 這裡把它簡化顯示成「1／2／3」
TERMINAL_MAP = {"第一航廈": "1", "第二航廈": "2", "第三航廈": "3"}
TYPE_MAP = {"A": "入境", "D": "出境"}

# 輸出到 flights_data_tpe.js 時，每一筆資料要包含的欄位（順序即輸出順序）
OUTPUT_KEYS = [
    "航廈", "航廈代碼", "種類", "種類代碼",
    "航空公司代碼", "航空公司代碼ICAO", "航空公司名", "班次", "機門",
    "表訂日期", "表訂時間", "表訂時間UTC",
    "預計日期", "預計時間", "預計時間UTC",
    "往來地點", "往來地點ICAO", "往來地點英文", "往來地點中文",
    "狀態", "機型",
    "其他航點", "其他航點ICAO", "其他航點英文", "其他航點中文",
    "行李轉盤", "報到櫃台",
]


def log(msg: str) -> None:
    """直接印到標準輸出，GitHub Actions 會把這些內容留在該次執行的 log 裡，
    不再另外寫成本機 log 檔案。"""
    print(msg, flush=True)


def _root_namespace(root: ET.Element) -> str:
    """取得 XML 根元素的命名空間網址（若有的話）。
    airport.xml / airline.xml 的根元素有宣告 xmlns（例如
    xmlns="https://ptx.transportdata.tw/standard/schema/"），底下所有標籤
    其實都帶著這個命名空間，ElementTree 比對標籤時必須完全比對含命名空間的
    完整標籤（"{命名空間}標籤名"）才抓得到，所以這裡直接從根元素的 tag
    動態取出命名空間網址，不寫死網址，之後來源網址若改變也不用動程式碼。
    """
    m = re.match(r"^\{(.*)\}", root.tag)
    return m.group(1) if m else ""


# ============================================================================
#  讀取 airport.xml -> 建立 AirportID -> AirportICAO 對照表
# ============================================================================
def load_airport_map() -> dict:
    mapping: dict[str, str] = {}
    if not AIRPORT_XML.exists():
        log(f"警告：找不到 {AIRPORT_XML}")
        return mapping
    tree = ET.parse(AIRPORT_XML)
    root = tree.getroot()
    ns = _root_namespace(root)
    tag = lambda name: f"{{{ns}}}{name}" if ns else name
    for airport in root.iter(tag("Airport")):
        airport_id = (airport.findtext(tag("AirportID")) or "").strip()
        airport_icao = (airport.findtext(tag("AirportICAO")) or "").strip()
        if airport_id:
            mapping[airport_id] = airport_icao
    log(f"airport.xml 載入完成，共 {len(mapping)} 筆機場代碼。")
    return mapping


# ============================================================================
#  讀取 airline.xml -> 建立 AirlineID -> AirlineICAO 對照表
# ============================================================================
def load_airline_map() -> dict:
    mapping: dict[str, str] = {}
    if not AIRLINE_XML.exists():
        log(f"警告：找不到 {AIRLINE_XML}")
        return mapping
    tree = ET.parse(AIRLINE_XML)
    root = tree.getroot()
    ns = _root_namespace(root)
    tag = lambda name: f"{{{ns}}}{name}" if ns else name
    for airline in root.iter(tag("Airline")):
        airline_id = (airline.findtext(tag("AirlineID")) or "").strip()
        airline_icao = (airline.findtext(tag("AirlineICAO")) or "").strip()
        if airline_id:
            mapping[airline_id] = airline_icao
    log(f"airline.xml 載入完成，共 {len(mapping)} 筆航空公司代碼。")
    return mapping


# ============================================================================
#  下載 CSV
# ============================================================================
def download_csv() -> str:
    headers = {"User-Agent": "Mozilla/5.0 (fetch_flights_tpe.py; +github-actions)"}
    resp = requests.get(CSV_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content.decode(CSV_CHARSET, errors="replace")


# ============================================================================
#  日期/時間正規化與 UTC 轉換
#  假設來源日期可能是 2026/07/12、2026-07-12 或 20260712 這幾種常見格式，
#  時間可能是 14:30 或 1430。若實際格式不同，只需調整這兩個函式。
# ============================================================================
def normalize_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    log(f"normalize_date() 無法辨識的日期格式：{raw!r}")
    return raw


def normalize_time(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) >= 4:
        return f"{digits[0:2]}:{digits[2:4]}"
    log(f"normalize_time() 無法辨識的時間格式：{raw!r}")
    return raw


def to_utc(date_str: str, time_str: str) -> str:
    """把「表訂/預計」日期+時間（台灣時間 UTC+8）轉成 UTC，輸出 ISO8601 格式字串（含 Z）"""
    d = normalize_date(date_str)
    t = normalize_time(time_str)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d) or not re.match(r"^\d{2}:\d{2}$", t):
        return ""
    try:
        local_dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ_TAIPEI)
    except ValueError:
        return ""
    utc_dt = local_dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================================
#  CSV 解析：依 column.txt 定義的 20 個欄位順序解析每一列
#  （來源看起來是逗號分隔、但每個欄位本身又有固定寬度的補空白，
#   這裡統一用 strip() 把每個欄位的補空白去掉）
# ============================================================================
def parse_csv_rows(text: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text))
    rows: list[list[str]] = []
    for raw_row in reader:
        row = [c.strip() for c in raw_row]
        if not row or all(c == "" for c in row):
            continue
        if len(row) < 20:
            log(f"略過欄位數不足20的資料列（共 {len(row)} 欄）：{row}")
            continue
        rows.append(row[:20])
    return rows


# ============================================================================
#  把20個原始欄位轉換成有意義的資料：代碼轉顯示值、比對機場/航空公司ICAO、
#  合併日期時間並轉為UTC
# ============================================================================
def build_record(row: list[str], airport_map: dict, airline_map: dict) -> dict:
    rec: dict[str, str] = {}

    # 欄位1 航廈：來源實際內容是「第一航廈/第二航廈/第三航廈」，簡化顯示成 1/2/3
    term_code = row[0]
    rec["航廈代碼"] = term_code
    rec["航廈"] = TERMINAL_MAP.get(term_code, term_code)

    # 欄位2 種類 代碼A:入境 代碼D:出境
    type_code = row[1]
    rec["種類代碼"] = type_code
    rec["種類"] = TYPE_MAP.get(type_code, type_code)

    # 欄位3/4 航空公司代碼 / 航空公司名 -> 比對 airline.xml 找 AirlineICAO
    airline_code = row[2]
    rec["航空公司代碼"] = airline_code
    rec["航空公司代碼ICAO"] = airline_map.get(airline_code, "")
    rec["航空公司名"] = row[3]

    # 欄位5/6 班次、機門
    rec["班次"] = row[4]
    rec["機門"] = row[5]

    # 欄位7/8 表訂日期、表訂時間 -> 正規化 + 轉 UTC
    rec["表訂日期"] = normalize_date(row[6])
    rec["表訂時間"] = normalize_time(row[7])
    rec["表訂時間UTC"] = to_utc(row[6], row[7])

    # 欄位9/10 預計日期、預計時間 -> 正規化 + 轉 UTC
    rec["預計日期"] = normalize_date(row[8])
    rec["預計時間"] = normalize_time(row[9])
    rec["預計時間UTC"] = to_utc(row[8], row[9])

    # 欄位11/12/13 往來地點（代碼）/英文/中文 -> 比對 airport.xml 找 AirportICAO
    from_to_code = row[10]
    rec["往來地點"] = from_to_code
    rec["往來地點ICAO"] = airport_map.get(from_to_code, "")
    rec["往來地點英文"] = row[11]
    rec["往來地點中文"] = row[12]

    # 欄位14/15 狀態、機型
    rec["狀態"] = row[13]
    rec["機型"] = row[14]

    # 欄位16/17/18 其他航點（代碼）/英文/中文 -> 比對 airport.xml 找 AirportICAO
    other_code = row[15]
    rec["其他航點"] = other_code
    rec["其他航點ICAO"] = airport_map.get(other_code, "")
    rec["其他航點英文"] = row[16]
    rec["其他航點中文"] = row[17]

    # 欄位19/20 行李轉盤、報到櫃台
    rec["行李轉盤"] = row[18]
    rec["報到櫃台"] = row[19]

    return rec


# ============================================================================
#  組出 flights_data_tpe.js 內容並寫檔
# ============================================================================
def write_output(records: list[dict]) -> None:
    now_taipei = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")

    OUTPUT_JS.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "// 自動產生檔案，請勿手動編輯 — 由 scripts/fetch_flights_tpe.py 產生",
        f"window.FLIGHT_DATA_TIME = {json.dumps(now_taipei, ensure_ascii=False)};",
        "window.FLIGHT_DATA = [",
    ]
    row_strs = []
    for rec in records:
        obj = {key: rec.get(key, "") for key in OUTPUT_KEYS}
        row_strs.append("  " + json.dumps(obj, ensure_ascii=False))
    lines.append(",\n".join(row_strs))
    lines.append("];")
    lines.append("")

    OUTPUT_JS.write_text("\n".join(lines), encoding="utf-8")


# ============================================================================
#  主流程
# ============================================================================
def main() -> int:
    log("開始抓取 CSV ...")

    airport_map = load_airport_map()
    airline_map = load_airline_map()

    try:
        csv_text = download_csv()
    except Exception as exc:  # noqa: BLE001 - 抓取失敗要能印出完整原因方便排查
        log(f"下載 CSV 失敗：{exc}")
        return 1

    rows = parse_csv_rows(csv_text)
    if not rows:
        log("CSV 內容解析後沒有任何資料列，請確認來源格式或 parse_csv_rows()/build_record() 是否需要調整。")

    records = [build_record(row, airport_map, airline_map) for row in rows]

    write_output(records)
    log(f"完成，共 {len(records)} 筆資料，已寫入 {OUTPUT_JS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
