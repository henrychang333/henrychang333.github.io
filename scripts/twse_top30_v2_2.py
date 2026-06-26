import os, time, requests
from datetime import datetime, timedelta
import pandas as pd
import json

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

# ════════════════════════════════════════════════════════════════
#  get_recent_trade_dates  回傳 [最新, ..., 最舊]
# ════════════════════════════════════════════════════════════════
def get_recent_trade_dates(n=5):
    trade_dates = []
    current_date = datetime.now()
    while len(trade_dates) < n:
        if current_date.weekday() < 5:
            date_str = current_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={date_str}&selectType=ALL"
            try:
                res = requests.get(url, headers=HEADERS, timeout=10)
                data = res.json()
                if res.status_code == 200 and data.get("stat") == "OK" and data.get("data"):
                    trade_dates.append(date_str)
                    time.sleep(1.2)
            except: pass
        current_date -= timedelta(days=1)
    return trade_dates

# ════════════════════════════════════════════════════════════════
#  load_watch_list
# ════════════════════════════════════════════════════════════════
def load_watch_list():
    watch_set = set()
    if os.path.exists("watch.txt"):
        with open("watch.txt", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip(): watch_set.add(line.strip())
    return watch_set

# ════════════════════════════════════════════════════════════════
#  fetch_closing_prices_for_date
#  使用 TWSE STOCK_DAY_ALL API 抓取指定日期全市場收盤價
#  注意：此 API 只有當天盤後才有資料，歷史日期需用月份查詢
#  回傳 {code: float}
# ════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════
#  fetch_closing_all_dates
#  逐月向 TWSE exchangeReport/STOCK_DAY_ALL 抓取全市場收盤價
#  修正：正確端點 + Session Referer + 雙格式解析 + JSONDecodeError 處理
#  回傳 {date_str: {code: float}}
# ════════════════════════════════════════════════════════════════
def _make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.twse.com.tw/zh/trading/historical/stock-day-all.html",
        "X-Requested-With": "XMLHttpRequest",
    })
    return s


# ════════════════════════════════════════════════════════════════
#  fetch_closing_all_dates  ← 取代 v6 的同名函式
#
#  改用 MI_INDEX（每日個股行情）API，格式較穩定
#  端點：https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX
#        ?response=json&date=YYYYMMDD&type=ALLBUT0999
#
#  MI_INDEX 回傳多個 tables，其中 table index=8（或欄位含「收盤價」）
#  才是個股行情，需逐 table 搜尋正確欄位
# ════════════════════════════════════════════════════════════════
def fetch_closing_all_dates(dates):
    """
    dates: [最新, ..., 最舊]  格式 YYYYMMDD
    逐日呼叫 MI_INDEX，解析個股收盤價
    回傳: {date_str: {code: float}}
    """
    closing_by_date = {}

    sess = requests.Session()
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Referer": "https://www.twse.com.tw/zh/trading/historical/mi-index.html",
        "X-Requested-With": "XMLHttpRequest",
    })

    for date_str in dates:
        url = (
            "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
            f"?response=json&date={date_str}&type=ALLBUT0999"
        )
        print(f"  ⬇️  收盤價 MI_INDEX {date_str}...")
        try:
            res = sess.get(url, timeout=15)
            if res.status_code != 200:
                print(f"  ⚠️  HTTP {res.status_code}，{date_str} 略過")
                time.sleep(1.5)
                continue

            body = res.json()
            if body.get("stat") != "OK":
                print(f"  ⚠️  stat={body.get('stat')}，{date_str} 無資料")
                time.sleep(1.5)
                continue

            # MI_INDEX 含多個 tables，找出含「收盤價」與「證券代號」的 table
            found = False
            for table in body.get("tables", []):
                fields = table.get("fields", [])
                # 找含收盤價的個股行情 table
                if "收盤價" not in fields or "證券代號" not in fields:
                    continue
                try:
                    code_idx  = fields.index("證券代號")
                    close_idx = fields.index("收盤價")
                except ValueError:
                    continue

                day_prices = {}
                for row in table.get("data", []):
                    code = str(row[code_idx]).strip()
                    try:
                        price = float(str(row[close_idx]).replace(",", ""))
                        day_prices[code] = price
                    except (ValueError, TypeError):
                        pass

                if day_prices:
                    closing_by_date[date_str] = day_prices
                    print(f"  ✅  {date_str} 收盤價筆數：{len(day_prices)}")
                    found = True
                    break

            if not found:
                # fallback：MI_INDEX 部分日期格式不同，fields 在最外層
                fields = body.get("fields", [])
                if "收盤價" in fields and "證券代號" in fields:
                    code_idx  = fields.index("證券代號")
                    close_idx = fields.index("收盤價")
                    day_prices = {}
                    for row in body.get("data", []):
                        code = str(row[code_idx]).strip()
                        try:
                            day_prices[code] = float(str(row[close_idx]).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    if day_prices:
                        closing_by_date[date_str] = day_prices
                        print(f"  ✅  {date_str} 收盤價筆數：{len(day_prices)}")
                        found = True

            if not found:
                print(f"  ⚠️  {date_str} 找不到收盤價 table，available fields={[t.get('fields',[]) for t in body.get('tables',[])]}")

        except ValueError as e:
            print(f"  ⚠️  JSON 解析失敗 {date_str}：{e}")
        except requests.exceptions.RequestException as e:
            print(f"  ⚠️  網路錯誤 {date_str}：{e}")

        time.sleep(1.8)

    return closing_by_date

def fetch_foreign_net(date_str):
    url = f"https://www.twse.com.tw/rwd/zh/fund/TWT38U?response=json&date={date_str}&selectType=ALL"
    result = {}
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            for row in res.json().get("data", []):
                code = str(row[1]).strip()
                if code.isalnum() and len(code) <= 8:
                    buy  = int(row[3].replace(",", ""))
                    sell = int(row[4].replace(",", ""))
                    result[code] = {
                        "buy": buy, "sell": sell,
                        "net": buy - sell,
                        "name": str(row[2]).strip()
                    }
    except Exception as e:
        print(f"⚠️  fetch_foreign_net({date_str}) 失敗：{e}")
    return result

# ════════════════════════════════════════════════════════════════
#  fetch_and_process_data  主流程
# ════════════════════════════════════════════════════════════════
def fetch_and_process_data():
    dates       = get_recent_trade_dates(5)  # [最新, ..., 最舊]
    watch_list  = load_watch_list()
    latest_date = dates[0]

    print(f"📅 最近 5 個交易日：{' / '.join(dates)}")

    # ── 1. 逐日抓取外資買賣超 ────────────────────────────────
    all_records       = []
    last_day_data     = {}
    daily_net_by_code = {}  # {code: {date_str: net}}

    for date_str in dates:
        print(f"  ⬇️  外資買賣超 {date_str}...")
        net_data = fetch_foreign_net(date_str)
        for code, info in net_data.items():
            all_records.append({"股票代號": code, "股票名稱": info["name"], "股數": info["net"]})
            if date_str == latest_date:
                last_day_data[code] = info
            daily_net_by_code.setdefault(code, {})[date_str] = info["net"]
        time.sleep(1.5)

    # ── 2. 計算 5 日累積買賣超排行 ───────────────────────────
    df = pd.DataFrame(all_records).groupby(["股票代號", "股票名稱"])["股數"].sum().reset_index()

    df_buy  = df[df["股數"] > 0].sort_values("股數", ascending=False).head(30).copy()
    df_buy.insert(0, "名次", [f"第 {i} 名" for i in range(1, len(df_buy)+1)])

    df_sell = df[df["股數"] < 0].sort_values("股數", ascending=True).head(30).copy()
    df_sell.insert(0, "名次", [f"第 {i} 名" for i in range(1, len(df_sell)+1)])

    # ── 3. 逐日抓取全市場收盤價 ──────────────────────────────
    closing_by_date = fetch_closing_all_dates(dates)
    latest_close    = closing_by_date.get(latest_date, {})

    # ── 4. 組合 price_history（由舊到新）────────────────────
    target_codes = set(df_buy["股票代號"]) | set(df_sell["股票代號"])
    price_history = {}
    for code in target_codes:
        rows = []
        for date_str in reversed(dates):  # 由舊到新
            rows.append({
                "date":  date_str,
                "price": closing_by_date.get(date_str, {}).get(code),   # None 若無資料
                "net":   daily_net_by_code.get(code, {}).get(date_str, 0)
            })
        price_history[code] = rows

    generate_html(df_buy, df_sell, dates, watch_list, last_day_data, latest_close, price_history)

# ════════════════════════════════════════════════════════════════
#  convert_rows
# ════════════════════════════════════════════════════════════════
def convert_rows(df, watch_list, last_day_data, latest_date, latest_close, price_history, is_buy=True):
    html = ""
    date_fmt = f"{latest_date[4:6]}/{latest_date[6:8]}"

    for _, r in df.iterrows():
        code         = str(r["股票代號"])
        total_shares = int(r["股數"])
        total_str    = f"{total_shares:,}" if is_buy else f"{abs(total_shares):,}"

        last_info = last_day_data.get(code, {"buy": 0, "sell": 0, "net": 0})
        last_buy  = f"{last_info['buy']:,}"
        last_sell = f"{last_info['sell']:,}"
        last_net  = last_info["net"]

        sign_html = ""
        if is_buy and last_net < 0:
            sign_html = "<div style='font-size:22px;color:#e63946;font-weight:bold;margin-left:10px;line-height:1.1;text-align:center;'>⚠️<br><span style='font-size:10px;'>轉賣</span></div>"
        elif not is_buy and last_net > 0:
            sign_html = "<div style='font-size:22px;color:#2b9348;font-weight:bold;margin-left:10px;line-height:1.1;text-align:center;'>⚠️<br><span style='font-size:10px;'>轉買</span></div>"

        style_class = 'class="highlight-row"' if code in watch_list else ""

        detail_html = f"""<div style='display:flex;align-items:center;justify-content:center;padding:2px 0;'>
            <div style='text-align:left;'>
                <strong style='font-size:14px;'>{total_str}</strong><br>
                <span class="last-day-info">({date_fmt} 買:{last_buy}/賣:{last_sell})</span>
            </div>{sign_html}
        </div>"""

        price     = latest_close.get(code)
        price_str = f"<span class='price-val'>{price:,.2f}</span>" if price is not None else "<span class='price-na'>N/A</span>"
        hist_json = json.dumps(price_history.get(code, []), ensure_ascii=False)
        code_link = (
            f"<span class='stock-link' "
            f"data-code='{code}' data-name='{r['股票名稱']}' "
            f"data-hist='{hist_json.replace(chr(39), '&apos;')}' "
            f"data-net='{total_shares}' onclick='showChart(this)'>{code}</span>"
        )

        html += f"<tr {style_class}>"
        html += (f"<td>{r['名次']}</td><td>{code_link}</td><td>{r['股票名稱']}</td>"
                 f"<td>{detail_html}</td><td class='price-cell'>{price_str}</td>")
        html += "</tr>"
    return html

# ════════════════════════════════════════════════════════════════
#  generate_html
# ════════════════════════════════════════════════════════════════
def generate_html(df_b, df_s, dates, watch_list, last_day_data, latest_close, price_history):
    b_rows    = convert_rows(df_b, watch_list, last_day_data, dates[0], latest_close, price_history, True)
    s_rows    = convert_rows(df_s, watch_list, last_day_data, dates[0], latest_close, price_history, False)
    range_str = (f"{dates[-1][:4]}/{dates[-1][4:6]}/{dates[-1][6:]} ~ "
                 f"{dates[0][:4]}/{dates[0][4:6]}/{dates[0][6:]}")

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>外資買賣超排行</title>
    <style>
        body {{ font-family: "Microsoft JhengHei", sans-serif; background: #f4f6f9; padding: 16px; margin: 0; }}
        .main-container {{ max-width: 1550px; margin: 0 auto; background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,.1); }}
        h1 {{ text-align:center; font-size:18px; margin:0 0 6px; }}
        .meta-info {{ text-align:center; color:#666; font-size:12px; margin-bottom:16px; line-height:1.6; }}
        .tables-wrapper {{ display:flex; gap:20px; }}
        .table-section {{ flex:1; min-width:0; }}
        .table-section h2 {{ text-align:center; padding:8px; color:#fff; border-radius:4px; margin:0 0 10px; font-size:14px; }}
        .sell-title {{ background:#2b9348; }} .buy-title {{ background:#d62828; }}
        .top30-table {{ width:100%; border-collapse:collapse; font-size:12px; }}
        .top30-table th, .top30-table td {{ padding:5px 7px; text-align:center !important; border-bottom:1px solid #ddd; line-height:1.4; }}
        .sell-theme th {{ background:#38b000; color:#fff; }}
        .buy-theme  th {{ background:#e63946; color:#fff; }}
        .top30-table tbody tr:nth-child(even) {{ background:#f9f9f9; }}
        .top30-table tbody tr.highlight-row {{ background:#fff2a3 !important; font-weight:bold; }}
        .last-day-info {{ font-size:10px; color:#888; display:block; margin-top:1px; }}
        .price-cell {{ white-space:nowrap; min-width:60px; }}
        .price-val {{ font-weight:bold; color:#1a1a2e; font-size:13px; }}
        .price-na  {{ color:#aaa; font-size:11px; }}
        .stock-link {{ color:#0066cc; text-decoration:underline; cursor:pointer; font-weight:bold; }}
        .stock-link:hover {{ color:#003d99; }}

        /* ── Modal：縮小固定高度，避免超出視窗 ── */
        #chartModal {{ display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:9999; align-items:center; justify-content:center; }}
        #chartModal.open {{ display:flex; }}
        #chartBox {{
            background:#fff; border-radius:10px; padding:16px;
            width:580px; max-width:95vw;
            max-height:92vh; overflow-y:auto;
            box-shadow:0 8px 32px rgba(0,0,0,.25); position:relative;
        }}
        #chartTitle {{ text-align:center; margin:0 0 3px; font-size:15px; color:#222; }}
        #chartSub   {{ text-align:center; margin:0 0 10px; font-size:11px; color:#999; }}
        #closeBtn   {{ position:absolute; top:8px; right:12px; font-size:20px; cursor:pointer; color:#666; background:none; border:none; }}
        #closeBtn:hover {{ color:#000; }}
        .legend-box {{ display:flex; justify-content:center; gap:16px; margin-top:8px; font-size:11px; color:#555; flex-wrap:wrap; }}
        .legend-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:3px; vertical-align:middle; }}
        #netAnn {{ text-align:center; margin:6px 0 0; font-size:12px; color:#555; }}
    </style>
</head>
<body>
<div class="main-container">
    <h1>📊 外資 5 日累積買賣超排行 (Top 30)</h1>
    <div class="meta-info">
        <strong>資料期間：</strong>{range_str}（共 5 個交易日）｜<strong>來源：</strong>TWSE API｜
        <strong>更新：</strong>{datetime.now().strftime('%Y/%m/%d %H:%M')}
    </div>
    <div class="tables-wrapper">
        <div class="table-section">
            <h2 class="sell-title">📉 外資賣超前 30 名</h2>
            <table class="top30-table sell-theme">
                <thead><tr><th>名次</th><th>代號</th><th>名稱</th><th>5日累積賣超股數</th><th>收盤價</th></tr></thead>
                <tbody>{s_rows}</tbody>
            </table>
        </div>
        <div class="table-section">
            <h2 class="buy-title">📈 外資買超前 30 名</h2>
            <table class="top30-table buy-theme">
                <thead><tr><th>名次</th><th>代號</th><th>名稱</th><th>5日累積買超股數</th><th>收盤價</th></tr></thead>
                <tbody>{b_rows}</tbody>
            </table>
        </div>
    </div>
</div>

<!-- ── Chart Modal ───────────────────────────── -->
<div id="chartModal">
  <div id="chartBox">
    <button id="closeBtn" onclick="closeChart()">✕</button>
    <h3 id="chartTitle"></h3>
    <p id="chartSub">實線＝收盤價（左軸）｜虛線＝外資淨買賣超（右軸，紅＝買超 綠＝賣超）</p>
    <canvas id="priceChart" height="260"></canvas>
    <div class="legend-box">
      <span><span class="legend-dot" style="background:#0066cc;"></span>收盤價（左軸）</span>
      <span><span class="legend-dot" style="background:#d62828;"></span>外資買超（右軸）</span>
      <span><span class="legend-dot" style="background:#2b9348;"></span>外資賣超（右軸）</span>
    </div>
    <p id="netAnn"></p>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script>
let chartInst = null;

function pointColors(nets) {{
  return nets.map(v => v == null ? 'transparent'
                     : v >= 0   ? 'rgba(214,40,40,0.8)'
                                : 'rgba(43,147,72,0.8)');
}}

function showChart(el) {{
  const code     = el.dataset.code;
  const name     = el.dataset.name;
  const hist     = JSON.parse(el.dataset.hist);  // [{{date,price,net}},...]
  const netTotal = parseInt(el.dataset.net, 10);

  if (!hist || hist.length === 0) {{ alert(code + ' 暫無資料'); return; }}

  const labels = hist.map(h => h.date.slice(4,6)+'/'+h.date.slice(6,8));
  const prices = hist.map(h => h.price);
  const nets   = hist.map(h => h.net || 0);
  const ptColors = pointColors(nets);

  document.getElementById('chartTitle').textContent = code + ' ' + name + ' ── 近 5 日';

  const ctx = document.getElementById('priceChart').getContext('2d');
  if (chartInst) chartInst.destroy();

  chartInst = new Chart(ctx, {{
    data: {{
      labels,
      datasets: [
        {{
          type: 'line',
          label: '收盤價 (元)',
          data: prices,
          borderColor: '#0066cc',
          backgroundColor: 'rgba(0,102,204,.07)',
          tension: 0.3,
          pointRadius: 5,
          pointHoverRadius: 7,
          pointBackgroundColor: '#0066cc',
          borderWidth: 2.5,
          spanGaps: true,
          fill: true,
          yAxisID: 'yP',
          order: 1
        }},
        {{
          type: 'line',
          label: '外資淨買賣超 (股)',
          data: nets,
          borderColor: ctx => {{
            const v = nets[ctx.dataIndex];
            return v >= 0 ? 'rgba(214,40,40,0.9)' : 'rgba(43,147,72,0.9)';
          }},
          segment: {{
            borderColor: ctx => {{
              const v = nets[ctx.p1DataIndex];
              return v >= 0 ? 'rgba(214,40,40,0.85)' : 'rgba(43,147,72,0.85)';
            }}
          }},
          backgroundColor: 'transparent',
          borderDash: [5, 3],
          borderWidth: 2,
          tension: 0.3,
          pointRadius: 5,
          pointHoverRadius: 7,
          pointBackgroundColor: ptColors,
          spanGaps: true,
          yAxisID: 'yN',
          order: 2
        }}
      ]
    }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          callbacks: {{
            label: ctx => {{
              if (ctx.dataset.yAxisID === 'yP')
                return ctx.parsed.y != null ? ' 收盤價：' + ctx.parsed.y.toFixed(2) + ' 元' : ' 收盤價：無資料';
              const v = ctx.parsed.y;
              return ' 外資' + (v >= 0 ? '買超' : '賣超') + '：' + Math.abs(v).toLocaleString() + ' 股';
            }}
          }}
        }}
      }},
      scales: {{
        yP: {{
          type: 'linear', position: 'left',
          title: {{ display: true, text: '收盤價 (元)', color: '#0066cc', font: {{ size: 11 }} }},
          ticks: {{ color: '#0066cc', font: {{ size: 10 }} }},
          grid: {{ color: 'rgba(0,102,204,.07)' }}
        }},
        yN: {{
          type: 'linear', position: 'right',
          title: {{ display: true, text: '外資淨買賣超 (股)', color: '#888', font: {{ size: 11 }} }},
          ticks: {{
            color: '#888', font: {{ size: 10 }},
            callback: v => (v >= 0 ? '+' : '') + v.toLocaleString()
          }},
          grid: {{ drawOnChartArea: false }}
        }}
      }}
    }}
  }});

  const sign   = netTotal >= 0 ? '📈 買超' : '📉 賣超';
  const absNet = Math.abs(netTotal).toLocaleString();
  document.getElementById('netAnn').innerHTML =
    '5 日累積外資 ' + sign + ' <strong>' + absNet + '</strong> 股';

  document.getElementById('chartModal').classList.add('open');
}}

function closeChart() {{
  document.getElementById('chartModal').classList.remove('open');
}}

document.getElementById('chartModal').addEventListener('click', function(e) {{
  if (e.target === this) closeChart();
}});
</script>
</body>
</html>"""

    with open("docs/top30_foreign_trade_v2.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("🎉 完成！HTML 報表已產生（全 TWSE API，雙折線圖，縮小版 Modal）。")


if __name__ == "__main__":
    fetch_and_process_data()
