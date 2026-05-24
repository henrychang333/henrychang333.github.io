import pandas as pd
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "stock_closing.csv")
HTML_PATH = os.path.join(BASE_DIR, "stock_report.html")
WATCH_PATH = os.path.join(BASE_DIR, "watch.txt")

df = pd.read_csv(CSV_PATH, encoding="utf-8-sig", dtype={"Code": str})

stock_data = {}
for (code, name), group in df.groupby(["Code", "Name"]):
    group = group.sort_values("Date")
    stock_data[code] = {
        "name": name,
        "dates": group["Date"].tolist(),
        "prices": group["ClosingPrice"].tolist()
    }

options_html = "\n".join(
    f'<option value="{code}">{code} {info["name"]}</option>'
    for code, info in sorted(stock_data.items())
)

# 讀取監控清單 TXT（每行一個 Code）
watch_codes = set()
if os.path.exists(WATCH_PATH):
    with open(WATCH_PATH, "r", encoding="utf-8") as f:
        for line in f:
            code = line.strip()
            if code:
                watch_codes.add(code)

html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>台股收盤價</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    body {{ font-family: Arial; padding: 15px; }}
    select {{ padding: 8px; font-size: 16px; width: 100%; max-width: 400px; }}
    #container {{ display: flex; flex-wrap: wrap; gap: 20px; margin-top: 20px; }}
    #left {{ width: 100%; max-width: 350px; }}
    #right {{ flex: 1; min-width: 300px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; text-align: center; }}
    th {{ background: #f2f2f2; }}
    #signalArea {{ margin-top: 30px; border-top: 2px solid #4caf50; padding-top: 15px; }}
    #warningArea {{ margin-top: 20px; border-top: 2px solid #f44336; padding-top: 15px; }}
    #signalList, #warningList {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .signal-item {{
      background: #e8f5e9;
      border: 1px solid #4caf50;
      padding: 6px 12px;
      border-radius: 4px;
      cursor: pointer;
    }}
    .signal-item:hover {{ background: #c8e6c9; }}
    .warning-item {{
      background: #ffebee;
      border: 1px solid #f44336;
      padding: 6px 12px;
      border-radius: 4px;
      cursor: pointer;
    }}
    .warning-item:hover {{ background: #ffcdd2; }}
    #updateTime {{ color: #888; font-size: 13px; margin-top: 5px; }}
  </style>
</head>
<body>
  <h2>台股股價查詢</h2>
  <p id="updateTime">資料產生時間：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>

  <select id="stockSelect" onchange="updateView()">
    <option value="">-- 請選擇股票 --</option>
    {options_html}
  </select>

  <div id="container">
    <div id="left">
      <h3>近5個月收盤價</h3>
      <table>
        <thead><tr><th>日期</th><th>收盤價</th></tr></thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
    <div id="right">
      <h3>近6個月均線圖</h3>
      <canvas id="maChart"></canvas>
    </div>
  </div>

  <div id="signalArea">
    <h3>📈 多頭排列股票</h3>
    <p style="color:#888; font-size:13px;">
      條件：MA8斜率 > MA21斜率 > MA55斜率（斜率皆為正）且當日 MA8 > MA21 > MA55
    </p>
    <div id="signalList"></div>
  </div>

  <div id="warningArea">
    <h3>⚠️ 死亡交叉警示（監控清單）</h3>
    <p style="color:#888; font-size:13px;">
      條件：MA8斜率 &lt; MA21斜率 &lt; MA55斜率，且 MA8-MA21 &lt; 2、MA8-MA55 &lt; 2
    </p>
    <div id="warningList"></div>
  </div>

  <script>
    const stockData = {json.dumps(stock_data, ensure_ascii=False)};
    const watchCodes = new Set({json.dumps(list(watch_codes))});
    let chartInstance = null;

    function parsePrice(v) {{
      return parseFloat(String(v).replace(/,/g, ""));
    }}

    function calcMA(prices, n) {{
      return prices.map((_, i) =>
        i < n - 1 ? null :
        (prices.slice(i-n+1, i+1).reduce((a,b) => a + parsePrice(b), 0) / n).toFixed(2)
      );
    }}

    function getSlope(arr) {{
      const valid = arr.filter(v => v !== null);
      if (valid.length < 2) return 0;
      return parseFloat(valid[valid.length-1]) - parseFloat(valid[valid.length-2]);
    }}

    function getLastValue(arr) {{
      const valid = arr.filter(v => v !== null);
      if (valid.length === 0) return null;
      return parseFloat(valid[valid.length-1]);
    }}

    function updateView() {{
      const code = document.getElementById("stockSelect").value;
      if (!code) return;
      const data = stockData[code];
      const dates = data.dates;
      const prices = data.prices;

      // 左側：近3個月（約65個交易日），由新到舊排列
      const recent  = dates.slice(-100);
      const recentP = prices.slice(-100);
      const tbody = document.getElementById("tableBody");
      tbody.innerHTML = "";
      [...recent].reverse().forEach((d, i) => {{
        const p = recentP[recentP.length - 1 - i];
        tbody.innerHTML += `<tr><td>${{d}}</td><td>${{p}}</td></tr>`;
      }});
      
      // ① 用全部資料計算均線（確保MA55有完整值）
      const ma8full  = calcMA(prices, 8);
      const ma21full = calcMA(prices, 21);
      const ma55full = calcMA(prices, 55);

      // 右側：近1年（約250個交易日）
      // const yearDates  = dates.slice(-250);
      // const yearPrices = prices.slice(-250);
      // ② 只取最後125筆顯示（近6個月）
      const N = 125;
      const yearDates  = dates.slice(-N);
      const yearPrices = prices.slice(-N);
      const ma8  = ma8full.slice(-N);
      const ma21 = ma21full.slice(-N);
      const ma55 = ma55full.slice(-N);
      
      // 垂直虛線
      const verticalLinePlugin = {{
        id: "verticalLine",
        afterDraw(chart) {{
          const tooltip = chart.tooltip;
          if (!tooltip || !tooltip.getActiveElements || !tooltip.getActiveElements().length) return;

          const ctx = chart.ctx;
          const x = tooltip.getActiveElements()[0].element.x;
          const {{ top, bottom }} = chart.chartArea;

          ctx.save();
          ctx.beginPath();
          ctx.moveTo(x, top);
          ctx.lineTo(x, bottom);
          ctx.lineWidth = 1;
          ctx.strokeStyle = "rgba(100,100,100,0.6)";
          ctx.setLineDash([5, 5]);
          ctx.stroke();
          ctx.restore();
        }}
      }};

      if (chartInstance) chartInstance.destroy();
      chartInstance = new Chart(document.getElementById("maChart"), {{
        type: "line",
        plugins: [verticalLinePlugin], 
        data: {{
          labels: yearDates,
          datasets: [
            {{ label: "收盤價", data: yearPrices.map(parsePrice), borderColor: "black", borderWidth: 2, pointRadius: 0 }},
            {{ label: "MA8",   data: ma8,                    borderColor: "blue",   borderWidth: 5, pointRadius: 0 }},
            {{ label: "MA21",  data: ma21,                   borderColor: "orange", borderWidth: 5, pointRadius: 0 }},
            {{ label: "MA55",  data: ma55,                   borderColor: "red",    borderWidth: 5, pointRadius: 0 }}
          ]
        }},
        options: {{
          responsive: true,
          interaction: {{
            mode: "index",
            intersect: false
          }},
          plugins: {{
            legend: {{ position: "top" }},
            tooltip: {{ enabled: true }}
          }},
          scales: {{ x: {{ ticks: {{ maxTicksLimit: 12 }} }} }}
        }}
      }});
    }}

    function buildSignalList() {{
      const signalList = document.getElementById("signalList");
      const warningList = document.getElementById("warningList");
      signalList.innerHTML = "";
      warningList.innerHTML = "";

      const signals = [];
      const warnings = [];

      for (const [code, data] of Object.entries(stockData)) {{
        const prices = data.prices;
        if (prices.length < 55) continue;

        const ma8  = calcMA(prices, 8);
        const ma21 = calcMA(prices, 21);
        const ma55 = calcMA(prices, 55);

        const s8  = getSlope(ma8);
        const s21 = getSlope(ma21);
        const s55 = getSlope(ma55);

        const v8  = getLastValue(ma8);
        const v21 = getLastValue(ma21);
        const v55 = getLastValue(ma55);

        if (v8 === null || v21 === null || v55 === null) continue;

        // ===== 多頭排列條件 =====
        // 1. 斜率：MA8 > MA21 > MA55，且皆為正
        // 2. 當日均線：MA8 > MA21 > MA55（黃金交叉）
        if (
          s8 > s21 && s21 > s55 &&
          s8 > 0 && s21 > 0 && s55 > 0 &&
          v8 < v21 && v8 < v55
        ) {{
          signals.push({{ code, name: data.name }});
        }}

        // ===== 死亡交叉警示條件（僅監控清單中的股票）=====
        // 1. 斜率：MA8 < MA21 < MA55
        // 2. MA8 - MA21 < 2（可為負）
        // 3. MA8 - MA55 < 2（可為負）
        if (watchCodes.has(code)) {{
          const diff_8_21 = v8 - v21;
          const diff_8_55 = v8 - v55;
          if (
            s8 < s21 && s21 < s55 &&
            diff_8_21 < 2 &&
            diff_8_55 < 2
          ) {{
            warnings.push({{
              code,
              name: data.name,
              diff_8_21: diff_8_21.toFixed(2),
              diff_8_55: diff_8_55.toFixed(2)
            }});
          }}
        }}
      }}

      // 顯示多頭排列
      if (signals.length === 0) {{
        signalList.innerHTML = "<p>目前無符合條件的股票</p>";
      }} else {{
        signals.sort((a, b) => a.code.localeCompare(b.code));
        signals.forEach(s => {{
          const div = document.createElement("div");
          div.className = "signal-item";
          div.textContent = `${{s.code}} ${{s.name}}`;
          div.onclick = () => {{
            document.getElementById("stockSelect").value = s.code;
            updateView();
            window.scrollTo({{ top: 0, behavior: "smooth" }});
          }};
          signalList.appendChild(div);
        }});
        const countEl = document.createElement("p");
        countEl.style.cssText = "width:100%; margin-top:10px; color:#555; font-size:13px;";
        countEl.textContent = `共 ${{signals.length}} 支股票符合條件`;
        signalList.appendChild(countEl);
      }}

      // 顯示死亡交叉警示
      if (watchCodes.size === 0) {{
        warningList.innerHTML = "<p style='color:#888'>watch.txt 為空或不存在</p>";
      }} else if (warnings.length === 0) {{
        warningList.innerHTML = "<p style='color:#888'>監控清單中目前無警示股票</p>";
      }} else {{
        warnings.sort((a, b) => a.code.localeCompare(b.code));
        warnings.forEach(w => {{
          const div = document.createElement("div");
          div.className = "warning-item";
          div.innerHTML = `
            <strong>${{w.code}} ${{w.name}}</strong><br>
            <small>MA8-MA21: ${{w.diff_8_21}} ｜ MA8-MA55: ${{w.diff_8_55}}</small>
          `;
          div.onclick = () => {{
            document.getElementById("stockSelect").value = w.code;
            updateView();
            window.scrollTo({{ top: 0, behavior: "smooth" }});
          }};
          warningList.appendChild(div);
        }});
        const countEl = document.createElement("p");
        countEl.style.cssText = "width:100%; margin-top:10px; color:#f44336; font-size:13px;";
        countEl.textContent = `共 ${{warnings.length}} 支股票觸發警示`;
        warningList.appendChild(countEl);
      }}
    }}

    buildSignalList();
  </script>
</body>
</html>"""

with open(HTML_PATH, "w", encoding="utf-8") as f:
    f.write(html)

print(f"完成！已產生 stock_report.html，共 {len(stock_data)} 支股票")
if watch_codes:
    print(f"監控清單：{len(watch_codes)} 支股票")
