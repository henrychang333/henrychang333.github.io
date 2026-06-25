import os, time, requests
from datetime import datetime, timedelta
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def get_recent_trade_dates(n=5):
    trade_dates = []
    current_date = datetime.now()
    while len(trade_dates) < n:
        if current_date.weekday() < 5:
            date_str = current_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={date_str}&selectType=ALL"
            try:
                res = requests.get(url, headers=HEADERS, timeout=10)
                if res.status_code == 200 and res.json().get("stat") == "OK" and res.json().get("data"):
                    trade_dates.append(date_str)
                    time.sleep(1.2)
            except: pass
        current_date -= timedelta(days=1)
    return trade_dates

def load_watch_list():
    watch_set = set()
    if os.path.exists("watch.txt"):
        with open("watch.txt", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): watch_set.add(line.strip())
    return watch_set

def fetch_and_process_data():
    dates = get_recent_trade_dates(5)
    watch_list = load_watch_list()
    
    all_records = []
    last_day_data = {}  # 用來安全儲存最後一日(最新一天)的資料
    latest_date_str = dates[0]
    
    for date_str in dates:
        url = f"https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={date_str}&selectType=ALL"
        try:
            res = requests.get(url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                for row in res.json().get("data", []):
                    code, name = str(row[1]).strip(), str(row[2]).strip()
                    if code.isalnum() and len(code) <= 8:
                        buy_shares = int(row[3].replace(",", ""))
                        sell_shares = int(row[4].replace(",", ""))
                        net_shares = int(row[5].replace(",", ""))
                        
                        all_records.append({"股票代號": code, "股票名稱": name, "股數": net_shares})
                        
                        # 如果是最新的一天，單獨存入字典
                        if date_str == latest_date_str:
                            last_day_data[code] = {
                                "buy": buy_shares, 
                                "sell": sell_shares, 
                                "net": buy_shares - sell_shares  # 強制用 買 - 賣 算出正負號
                            }
        except: pass
        time.sleep(1.5)
        
    df = pd.DataFrame(all_records).groupby(["股票代號", "股票名稱"])["股數"].sum().reset_index()
    
    df_buy = df[df["股數"] > 0].sort_values(by="股數", ascending=False).head(30)
    df_buy.insert(0, "名次", [f"第 {i} 名" for i in range(1, len(df_buy) + 1)])
    
    df_sell = df[df["股數"] < 0].sort_values(by="股數", ascending=True).head(30)
    df_sell.insert(0, "名次", [f"第 {i} 名" for i in range(1, len(df_sell) + 1)])
    
    generate_html(df_buy, df_sell, dates, watch_list, last_day_data)

def convert_rows(df, watch_list, last_day_data, latest_date, is_buy=True):
    html = ""
    date_formatted = f"{latest_date[4:6]}/{latest_date[6:8]}"
    for _, r in df.iterrows():
        code = str(r["股票代號"])
        total_shares = int(r["股數"])
        total_str = f"{total_shares:,}" if is_buy else f"{abs(total_shares):,}"
        
        last_info = last_day_data.get(code, {"buy": 0, "sell": 0, "net": 0})
        last_buy = f"{last_info['buy']:,}"
        last_sell = f"{last_info['sell']:,}"
        last_net = last_info['net']
        
        # 1. 定義放大顯示的圖示與字樣（移到最右側）
        sign_html = ""
        if is_buy and last_net < 0:
            sign_html = "<div style='font-size: 24px; color: #e63946; font-weight: bold; margin-left: 12px; line-height: 1.1; text-align: center;'>⚠️<br><span style='font-size: 11px;'>轉賣</span></div>"
        elif not is_buy and last_net > 0:
            sign_html = "<div style='font-size: 24px; color: #2b9348; font-weight: bold; margin-left: 12px; line-height: 1.1; text-align: center;'>⚠️<br><span style='font-size: 11px;'>轉買</span></div>"
            
        style_class = 'class="highlight-row"' if code in watch_list else ""
        
        # 2. 使用 Flexbox 布局，將 sign_html 放置於數據的最後方（右側）
        detail_html = f"""<div style='display: flex; align-items: center; justify-content: center; padding: 2px 0;'>
            <div style='text-align: left;'>
                <strong style='font-size: 15px;'>{total_str}</strong><br>
                <span class="last-day-info">(最後交易日{date_formatted}---買:{last_buy} / 賣:{last_sell})</span>
            </div>
            {sign_html}
        </div>"""
        
        html += f"<tr {style_class}>"
        html += f"<td>{r['名次']}</td><td>{code}</td><td>{r['股票名稱']}</td><td>{detail_html}</td>"
        html += "</tr>"
    return html

def generate_html(df_b, df_s, dates, watch_list, last_day_data):
    b_rows = convert_rows(df_b, watch_list, last_day_data, dates[0], is_buy=True)
    s_rows = convert_rows(df_s, watch_list, last_day_data, dates[0], is_buy=False)
    
    b_col_name = "5日累積外資買超股數"
    s_col_name = "5日累積外資賣超股數"
    
    range_str = f"{dates[-1][0:4]}/{dates[-1][4:6]}/{dates[-1][6:8]} ~ {dates[0][0:4]}/{dates[0][4:6]}/{dates[0][6:8]}"
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>外資買賣超排行</title>
    <style>
        body {{ font-family: "Microsoft JhengHei", sans-serif; background: #f4f6f9; padding: 20px; }}
        .main-container {{ max-width: 1450px; margin: 0 auto; background: #fff; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        h1, .meta-info {{ text-align: center; }}
        .meta-info {{ color: #666; font-size: 14px; margin-bottom: 25px; line-height: 1.6; }}
        .tables-wrapper {{ display: flex; justify-content: space-between; gap: 30px; }}
        .table-section {{ flex: 1; }}
        .table-section h2 {{ text-align: center; padding: 10px; color: white; border-radius: 4px; margin-bottom: 15px; }}
        .sell-title {{ background: #2b9348; }} .buy-title {{ background: #d62828; }}
        .top30-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
        .top30-table th, .top30-table td {{ padding: 8px 10px; text-align: center !important; border-bottom: 1px solid #ddd; line-height: 1.5; }}
        .sell-theme th {{ background: #38b000; color: white; }}
        .buy-theme th {{ background: #e63946; color: white; }}
        .top30-table tbody tr:nth-child(even) {{ background: #f9f9f9; }}
        .top30-table tbody tr.highlight-row {{ background: #fff2a3 !important; font-weight: bold; }}
        .last-day-info {{ font-size: 11px; color: #666; font-weight: normal; display: block; margin-top: 2px; }}
        .top30-table tbody tr.reverse-sell-row td {{ background-color: #ffe5d9 !important; }}
        .top30-table tbody tr.reverse-buy-row td {{ background-color: #e8f5e9 !important; }}
    </style>
</head>
<body>
<div class="main-container">
    <h1>📊 外資 5 日累積買賣超排行 (Top 30)</h1>
    <div class="meta-info"><strong>資料期間：</strong> {range_str} (共 5 個交易日)<br><strong>最後更新：</strong> {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}</div>
    <div class="tables-wrapper">
        <div class="table-section">
            <h2 class="sell-title">📉 外資賣超排行前 30 名</h2>
            <table class="top30-table sell-theme"><thead><tr><th>名次</th><th>股票代號</th><th>股票名稱</th><th>{s_col_name}</th></tr></thead><tbody>{s_rows}</tbody></table>
        </div>
        <div class="table-section">
            <h2 class="buy-title">📈 外資買超排行前 30 名</h2>
            <table class="top30-table buy-theme"><thead><tr><th>名次</th><th>股票代號</th><th>股票名稱</th><th>{b_col_name}</th></tr></thead><tbody>{b_rows}</tbody></table>
        </div>
    </div>
</div>
</body>
</html>"""
    with open("docs/top30_foreign_trade.html", "w", encoding="utf-8") as f: f.write(html)
    print("🎉 執行成功！已產生最新修正版 HTML 報表。")

if __name__ == "__main__": fetch_and_process_data()
