#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_data.py
================
高雄機場出境電子看板 - 後端資料更新程式（Python 版本）
"""

import os
import sys
import re
import json
import argparse
import time
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import requests

# 基礎路徑設定
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".cache" / "khfids"
OUTPUT_FILE = CACHE_DIR / "data.js"
OUTPUT_TMP = CACHE_DIR / "data.js.tmp"

# 專案根目錄下的 docs/ 資料夾
DOCS_DIR = BASE_DIR.parent / "docs"
DOCS_DATA_FILE = DOCS_DIR / "flights_data_kh.js"

# 資料來源網址
DEP_URL = "https://ccc.kia.gov.tw/fids/json/TT/dep.php"

# 對照表檔案路徑（已依據需求改為指向 repo 根目錄下的 data 子資料夾）
AIRPORT_XML_PATH = BASE_DIR.parent / "data" / "airport.xml"
OVERRIDE_FILE_PATH = BASE_DIR.parent / "data" / "iata_icao_override.json"


def log(msg: str):
    now = datetime.now().isoformat()
    line = f"[{now}] {msg}"
    print(line)


def load_override_map(path: Path) -> dict:
    override_map = {}
    if not path.exists():
        return override_map
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in data.items():
                if not k.startswith("_"):
                    override_map[k.strip().upper()] = v.strip().upper()
    except Exception as e:
        log(f"載入 iata_icao_override.json 失敗: {e}")
    return override_map


def load_airport_xml_map(path: Path) -> dict:
    airport_map = {}
    if not path.exists():
        log(f"警告：找不到 airport.xml，預期路徑為: {path}")
        return airport_map

    try:
        tree = ET.parse(path)
        root = tree.getroot()
        
        ns = ""
        m = re.match(r'\{.*\}', root.tag)
        if m:
            ns = m.group(0)
            
        for airport in root.findall(f'{ns}Airport'):
            iata_node = airport.find(f'{ns}AirportIATA')
            icao_node = airport.find(f'{ns}AirportICAO')
            
            if iata_node is not None and icao_node is not None:
                iata = (iata_node.text or "").strip().upper()
                icao = (icao_node.text or "").strip().upper()
                if iata and icao:
                    airport_map[iata] = icao
                    
        log(f"airport.xml 載入完成，共解析出 {len(airport_map)} 筆機場對照資料。")
    except Exception as e:
        log(f"解析 airport.xml 失敗: {e}")
        
    return airport_map


def convert_airline_num(num_str: str, airline_code: str) -> str:
    if not num_str:
        return ""
    code = (airline_code or "").strip()
    if not code:
        return num_str
    return code + num_str[2:]


def resolve_arrival_icao(iata3: str, api_icao: str, override_map: dict, airport_xml_map: dict) -> str:
    c = (iata3 or "").strip().upper()
    effective_icao = (api_icao or "").strip().upper()

    if c in override_map:
        return override_map[c]

    if len(effective_icao) == 3:
        if effective_icao in airport_xml_map:
            return airport_xml_map[effective_icao]

    if c in airport_xml_map:
        return airport_xml_map[c]

    if effective_icao:
        return effective_icao

    return c


def fetch_json(url: str) -> list:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; KIA-FIDS-Python/1.0)",
        "Accept": "application/json, text/plain, */*"
    }
    
    resp = requests.get(url, headers=headers, timeout=15, verify=False)
    resp.raise_for_status()
    
    # 解決 Unexpected UTF-8 BOM 錯誤
    text_content = resp.content.decode('utf-8-sig')
    return json.loads(text_content)


def build_dashboard_data() -> dict:
    raw_data = fetch_json(DEP_URL)
    override_map = load_override_map(OVERRIDE_FILE_PATH)
    airport_xml_map = load_airport_xml_map(AIRPORT_XML_PATH)

    today_str = datetime.now().strftime("%Y%m%d")

    processed_flights = []
    for item in raw_data:
        fdate_raw = item.get("FDATE", "")
        fdate_digits = re.sub(r"\D", "", fdate_raw)[:8]
        if fdate_digits != today_str:
            continue

        airline_num_orig = item.get("airLineNum", "")
        airline_code = item.get("airLineCode", "")
        arrival_iata = item.get("ArrivalAirportIATA", "")
        arrival_api_icao = item.get("ArrivalAirportICAO", "")

        airline_num = convert_airline_num(airline_num_orig, airline_code)
        arrival_icao = resolve_arrival_icao(arrival_iata, arrival_api_icao, override_map, airport_xml_map)
        status_dep = item.get("status_dep", item.get("statusdep", ""))

        processed_flights.append({
            "airLineNum": airline_num,
            "airLineIATA": item.get("airLineIATA", ""),  # 已新增擷取欄位
            "REG_NO": item.get("REG_NO", ""),
            "ArrivalAirportICAO": arrival_icao,
            "ArrivalAirportIATA": arrival_iata,
            "STD": item.get("STD", ""),
            "amhsETD": item.get("amhsETD", ""),
            "status_dep": status_dep,
            "AOBT": item.get("AOBT", ""),
            "ATD": item.get("ATD", ""),
            "Bay": item.get("Bay", "")
        })

    processed_flights.sort(key=lambda x: x["STD"])

    return {
        "updated_at": datetime.now().astimezone().isoformat(timespec='seconds'),
        "flight_date": today_str,
        "count": len(processed_flights),
        "flights": processed_flights
    }


def write_output(data: dict):
    js_text = f"window.FIDS_DATA = {json.dumps(data, ensure_ascii=False, indent=2)};\n"

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_TMP, "w", encoding="utf-8") as f:
        f.write(js_text)
    if OUTPUT_FILE.exists():
        os.remove(OUTPUT_FILE)
    os.rename(OUTPUT_TMP, OUTPUT_FILE)

    if DOCS_DIR.exists():
        docs_tmp = DOCS_DIR / "flights_data_kh.js.tmp"
        with open(docs_tmp, "w", encoding="utf-8") as f:
            f.write(js_text)
        if DOCS_DATA_FILE.exists():
            os.remove(DOCS_DATA_FILE)
        os.rename(docs_tmp, DOCS_DATA_FILE)


def run_once() -> bool:
    try:
        data = build_dashboard_data()
        write_output(data)
        log(f"更新成功，共 {data['count']} 筆航班")
        return True
    except Exception as e:
        log(f"更新失敗：{e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="高雄機場出境電子看板 - 資料更新程式")
    parser.add_argument(
        "--loop", type=int, default=0,
        help="以迴圈方式每 N 秒更新一次"
    )
    args = parser.parse_args()

    if args.loop > 0:
        log(f"以迴圈模式啟動，每 {args.loop} 秒更新一次...")
        while True:
            run_once()
            time.sleep(args.loop)
    else:
        success = run_once()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()