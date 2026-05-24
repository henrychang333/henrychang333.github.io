import requests
import pandas as pd
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, "stock_closing.csv")

def get_all_stocks():
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    data = requests.get(url, verify=False).json()
    return [{"Code": s["Code"], "Name": s["Name"]} for s in data]

def get_last_date(code):
    if not os.path.exists(CSV_FILE):
        return None
    df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype={"Code": str})
    sub = df[df["Code"] == code]
    if sub.empty:
        return None
    return sub["Date"].max()

def fetch_stock_day(code, yyyymm):
    url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={yyyymm}01&stockNo={code}"
    try:
        res = requests.get(url, verify=False, timeout=10).json()
        if res.get("stat") != "OK":
            return []
        fields = res["fields"]
        rows = res["data"]
        date_idx = fields.index("日期")
        close_idx = fields.index("收盤價")
        return [{
            "Date": r[date_idx],
            "ClosingPrice": r[close_idx].replace(",", "")  # ← 加這行
        } for r in rows]
    except Exception as e:
        print(f"  抓取失敗：{code} {yyyymm}，原因：{e}")
        return []

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 開始執行...")

    stocks = get_all_stocks()
    print(f"共 {len(stocks)} 支股票")

    # ★ 一次性讀取CSV，建立查詢字典（只讀一次）
    last_date_dict = {}
    if os.path.exists(CSV_FILE):
        print("讀取現有資料中...")
        old_df = pd.read_csv(CSV_FILE, encoding="utf-8-sig", dtype={"Code": str})
        last_date_dict = old_df.groupby("Code")["Date"].max().to_dict()
        print(f"已載入 {len(old_df)} 筆現有資料")

    all_rows = []
    today = datetime.today()
    months = [(today - relativedelta(months=i)).strftime("%Y%m") for i in range(1, -1, -1)]

    for i, stock in enumerate(stocks):
        code = stock["Code"]
        name = stock["Name"]
        print(f"處理中 {i+1}/{len(stocks)}: {code} {name}")

        # ★ 直接從字典查詢，不再重複讀取CSV
        last_date = last_date_dict.get(code, None)

        for ym in months:
            if last_date and ym <= last_date[:7].replace("-", ""):
                continue
            records = fetch_stock_day(code, ym)
            for r in records:
                all_rows.append({
                    "Code": code,
                    "Name": name,
                    "Date": r["Date"],
                    "ClosingPrice": r["ClosingPrice"]
                })
            time.sleep(0.3)

    if all_rows:
        new_df = pd.DataFrame(all_rows)
        if os.path.exists(CSV_FILE):
            df = pd.concat([old_df, new_df]).drop_duplicates(subset=["Code", "Date"])
        else:
            df = new_df
        df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 完成！新增 {len(new_df)} 筆資料")
    else:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 無新資料需要更新")

main()