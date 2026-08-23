# -*- coding: utf-8 -*-
"""
匯率報表（Chart.js 版）

分工：
- 匯率.py 只負責「下載資料 + 整理資料」，用 Playwright 無頭瀏覽器
  從台銀抓歷史匯率（台銀現有 JS 反爬蟲驗證，純 HTTP 會被擋）。
- 走勢圖改由 rate_report.html 內嵌的 Chart.js 在瀏覽器渲染。
- 輸出單一自包含的 rate_report.html（Chart.js 一併內嵌，離線可看），
  不再產生 PNG，也不依賴 matplotlib。

安裝（在 Anaconda Prompt 執行一次）：
    pip install playwright
    python -m playwright install chromium

執行：
    python 匯率.py
"""

import os
import json
import time
import urllib.request
import webbrowser
from datetime import datetime, date

from playwright.sync_api import sync_playwright

# === 參數設定 ===

CURRENCIES = ["AUD", "JPY", "USD", "EUR", "SGD"]

NAMES_ZH = {
    "USD": "美元",
    "EUR": "歐元",
    "AUD": "澳幣",
    "SGD": "新加坡幣",
    "JPY": "日圓",
}

# Chart.js 用的顏色（CSS 色碼）
COLORS = {
    "USD": "rgba(220, 38, 38, 1)",     # red
    "EUR": "rgba(37, 99, 235, 1)",     # blue
    "AUD": "rgba(22, 163, 74, 1)",     # green
    "SGD": "rgba(249, 115, 22, 1)",   # orange
    "JPY": "rgba(147, 51, 234, 1)",   # purple
}

THIS_YEAR = date.today().year
START_DATE = date(THIS_YEAR, 1, 1)

# 台銀原始歷史匯率頁面（最近一年）網址格式
BASE_URL = "https://rate.bot.com.tw/xrt/quote/ltm/{}"

# 台銀歷史牌告匯率：指定年月 + 幣別
# 例：https://rate.bot.com.tw/xrt/quote/2026-08/USD?Lang=zh-TW
QUOTE_URL = "https://rate.bot.com.tw/xrt/quote/{ym}/{cur}?Lang=zh-TW"

# 反爬蟲驗證通過後才會出現的表格 selector
TABLE_SELECTOR = 'table[title="歷史本行營業時間牌告匯率"] tbody tr'

# Chart.js（首次執行下載到本機快取，之後離線也能內嵌）
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"
CHART_JS_LOCAL = "chart.umd.min.js"


# === 資料擷取（Playwright，取代 twder） ===

class RateFetcher:
    """以無頭瀏覽器擷取台銀歷史匯率，回傳格式與 twder.specify_month 相同。"""

    def __init__(self):
        self._pw = None
        self._browser = None
        self._context = None

    def start(self):
        self._pw = sync_playwright().start()
        # headless=False 可在除錯時觀察；正常使用保持 True
        self._browser = self._pw.chromium.launch(headless=True)
        # 共用 context：第一次解完驗證後 cookie 會保留，之後不再卡驗證頁
        self._context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36"
        )

    def stop(self):
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def _is_challenge(self, page):
        """判斷目前是否停在反爬蟲驗證頁。"""
        try:
            title = page.title()
        except Exception:
            return False
        return "Challenge" in title or "challenge" in title

    def specify_month(self, currency, year, month):
        """
        回傳 [(日期, 現金買入, 現金賣出, 即期買入, 即期賣出), ...]
        與 twder.specify_month 相容；日期由舊到新。
        """
        ym = f"{year}-{month:02d}"
        url = QUOTE_URL.format(ym=ym, cur=currency)

        max_retry = 4        # 最多重試次數
        backoff = 3          # 每次重試前等待秒數

        page = self._context.new_page()
        try:
            for attempt in range(1, max_retry + 1):
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # 若碰到 Challenge Validation 驗證頁，瀏覽器會執行 JS 自動解題並 reload；
                # 這裡等到真正的表格列出現為止。
                try:
                    page.wait_for_selector(TABLE_SELECTOR, timeout=30000)
                    got_table = True
                except Exception:
                    got_table = False

                if got_table:
                    break

                # 沒拿到表格：可能是卡在驗證頁，或該月真的沒資料
                if self._is_challenge(page):
                    if attempt < max_retry:
                        print(f"  [重試 {attempt}/{max_retry-1}] {currency} {ym} "
                              f"卡在反爬蟲驗證頁，等待 {backoff}s 後重新載入...")
                        time.sleep(backoff)
                        # reload 讓 JS 再跑一次解題（此時 cookie 多半已設好）
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=60000)
                            page.wait_for_selector(TABLE_SELECTOR, timeout=30000)
                            break
                        except Exception:
                            continue
                    else:
                        print(f"  [警告] {currency} {ym} 重試 {max_retry} 次仍卡在反爬蟲驗證頁，"
                              f"請稍後重試，或改 headless=False 觀察。")
                        return []
                else:
                    # 非驗證頁卻沒表格 → 該月尚無牌告資料
                    return []

            rows = page.query_selector_all(TABLE_SELECTOR)
            result = []
            for r in rows:
                tds = r.query_selector_all("td")
                if len(tds) < 6:
                    continue
                date_str = tds[0].inner_text().strip()
                cash_buy = tds[2].inner_text().strip()
                cash_sell = tds[3].inner_text().strip()
                spot_buy = tds[4].inner_text().strip()
                spot_sell = tds[5].inner_text().strip()
                result.append((date_str, cash_buy, cash_sell, spot_buy, spot_sell))
            # 頁面是由新到舊排列，這裡反轉成由舊到新
            result.reverse()
            return result
        finally:
            page.close()


# === 資料整理 ===

def fetch_from_this_year(fetcher, cur):
    """
    從今年 1/1 起，逐月抓取「現金賣出」資料。
    回傳 (dates, values)，皆為字串/浮點數 list，日期由舊到新。
    """
    all_dates = []
    all_values = []

    today = date.today()
    for i, m in enumerate(range(1, today.month + 1)):
        if i > 0:
            time.sleep(1)   # 月份間稍作停頓，降低觸發反爬蟲驗證的機率
        data = fetcher.specify_month(cur, THIS_YEAR, m)
        # data: [(日期, 現金買入, 現金賣出, 即期買入, 即期賣出), ...]
        for row in data:
            date_str, cash_buy, cash_sell, spot_buy, spot_sell = row
            if not cash_sell or cash_sell == "-":
                continue
            try:
                d = datetime.strptime(date_str, "%Y/%m/%d").date()
                if d < START_DATE:
                    continue
                v = float(cash_sell)
            except ValueError:
                continue
            all_dates.append(d.strftime("%Y/%m/%d"))
            all_values.append(v)

    if not all_dates:
        return [], []
    # 確保由舊到新
    paired = sorted(zip(all_dates, all_values))
    all_dates = [p[0] for p in paired]
    all_values = [p[1] for p in paired]
    return all_dates, all_values


def check_rule(values):
    """
    依條件：(平均值 - 當年最低值) / 3 + 當年最低值 > 今日掛牌值
    回傳 (warn_flag, detail_dict)。
    """
    if not values:
        return False, None
    avg_val = sum(values) / len(values)
    min_val = min(values)
    max_val = max(values)
    today_val = values[-1]  # 視最後一筆為今日掛牌值
    cond_val = (avg_val - min_val) / 3 + min_val
    detail = {
        "avg": avg_val,
        "min": min_val,
        "max": max_val,
        "today": today_val,
        "cond": cond_val
    }
    return cond_val > today_val, detail


# === HTML 產生（Chart.js 渲染，自包含單一檔案） ===

def ensure_chart_js():
    """下載 Chart.js 到本機快取（首次執行），之後離線也能內嵌。"""
    if not os.path.exists(CHART_JS_LOCAL):
        print("首次執行：下載 Chart.js ...")
        urllib.request.urlretrieve(CHART_JS_URL, CHART_JS_LOCAL)
    with open(CHART_JS_LOCAL, "r", encoding="utf-8") as f:
        return f.read()


def generate_html(results, chart_js_content):
    """
    產出單一自包含的 rate_report.html：
    - 表格：幣別(含連到台銀的超連結)、平均值、最低、最高、今日、條件值、警示。
    - 表格上方顯示今日掛牌值日期。
    - 各幣別走勢圖由內嵌的 Chart.js 渲染。
    """
    # 找一個有資料的幣別，拿它的 last_date 當「今日掛牌值日期」
    last_date_for_all = None
    for r in results:
        if r.get("detail") and r.get("last_date"):
            last_date_for_all = r["last_date"]
            break

    if last_date_for_all:
        date_text = f"本表『今日掛牌值』使用資料最後一日：{last_date_for_all}"
    else:
        date_text = "本表『今日掛牌值』日期：無有效資料"

    # 組表格列
    html_rows = []
    for r in results:
        cur = r["currency"]
        name_zh = NAMES_ZH.get(cur, cur)
        url = BASE_URL.format(cur)

        if not r["detail"]:
            row = f"""
            <tr>
              <td>
                <a href="{url}" target="_blank">
                  {name_zh} ({cur})
                </a>
              </td>
              <td colspan="5">無資料</td>
              <td>-</td>
            </tr>
            """
            html_rows.append(row)
            continue

        d = r["detail"]
        warn_text = "警示：(平均值 - 當年最低值)/3 + 當年最低值 > 今日掛牌值" if r["warn"] else "條件未達成"
        warn_color = "red" if r["warn"] else "black"

        row = f"""
        <tr>
          <td>
            <a href="{url}" target="_blank">
              {name_zh} ({cur})
            </a>
          </td>
          <td>{d['avg']:.4f}</td>
          <td>{d['min']:.4f}</td>
          <td>{d['max']:.4f}</td>
          <td>{d['today']:.4f}</td>
          <td>{d['cond']:.4f}</td>
          <td style="color:{warn_color};">{warn_text}</td>
        </tr>
        """
        html_rows.append(row)

    # 準備給 Chart.js 的資料集（只有有資料的幣別）
    chart_datasets = []
    for r in results:
        if not r.get("dates") or not r.get("values"):
            continue
        cur = r["currency"]
        name_zh = NAMES_ZH.get(cur, cur)
        chart_datasets.append({
            "currency": cur,
            "label": f"{name_zh} ({cur}) 現金賣出",
            "color": COLORS.get(cur, "rgba(0,0,0,1)"),
            "dates": r["dates"],
            "values": r["values"],
            "warn": r["warn"],
        })

    # 嵌入資料：跳脫 < 避免破壞 <script>
    datasets_json = json.dumps(chart_datasets, ensure_ascii=False).replace("<", "\\u003c")
    # 內嵌 Chart.js 前，處理內容中可能的 </script> 序列，避免 HTML parser 提前結束 script
    chart_js_block = chart_js_content.replace("</script", "<\\/script")

    # 各幣別圖的 canvas 容器
    img_blocks = []
    for r in results:
        cur = r["currency"]
        name_zh = NAMES_ZH.get(cur, cur)
        if not r.get("dates") or not r.get("values"):
            continue
        title_extra = "（警示條件成立）" if r["warn"] else ""
        img_blocks.append(f"""
        <h3>{name_zh} ({cur}){title_extra}</h3>
        <div style="height: 360px;">
          <canvas id="chart_{cur}"></canvas>
        </div>
        <hr>
        """)

    html = (
        "<!doctype html>\n"
        "<html lang=\"zh-Hant\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        f"  <title>{THIS_YEAR} 年匯率報表</title>\n"
        "  <style>\n"
        "    body { font-family: Arial, \"Microsoft JhengHei\", sans-serif; margin: 20px; }\n"
        "    table { border-collapse: collapse; margin-top: 20px; width: 80%; }\n"
        "    th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: center; }\n"
        "    th { background: #f3f3f3; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"  <h2>{THIS_YEAR} 年 1 月 1 日起 台灣銀行現金匯率－本行賣出 報表</h2>\n"
        f"  <p>{date_text}</p>\n"
        "  <h3>統計與警示條件（(平均值 - 當年最低值)/3 + 當年最低值 > 今日掛牌值）</h3>\n"
        "  <table>\n"
        "    <thead>\n"
        "      <tr>\n"
        "        <th>幣別</th>\n"
        "        <th>平均值</th>\n"
        "        <th>當年最低值</th>\n"
        "        <th>當年最高值</th>\n"
        "        <th>今日掛牌值</th>\n"
        "        <th>(平均值 - 最低值) / 3 + 最低值</th>\n"
        "        <th>警示</th>\n"
        "      </tr>\n"
        "    </thead>\n"
        "    <tbody>\n"
        + "".join(html_rows)
        + "    </tbody>\n"
        "  </table>\n"
        "\n"
        "  <h3>各幣別走勢圖</h3>\n"
        + "".join(img_blocks)
        + "\n"
        # 內嵌 Chart.js（離線可用）
        "<script>\n"
        + chart_js_block
        + "\n</script>\n"
        # 資料 + 渲染邏輯
        "<script>\n"
        f"const __DATASETS__ = {datasets_json};\n"
        "window.addEventListener('DOMContentLoaded', function () {\n"
        "  __DATASETS__.forEach(function (ds) {\n"
        "    const ctx = document.getElementById('chart_' + ds.currency);\n"
        "    if (!ctx) return;\n"
        "    new Chart(ctx, {\n"
        "      type: 'line',\n"
        "      data: {\n"
        "        labels: ds.dates,\n"
        "        datasets: [{\n"
        "          label: ds.label,\n"
        "          data: ds.values,\n"
        "          borderColor: ds.color,\n"
        "          backgroundColor: ds.color.replace(/,\s*1\)$/, ', 0.12)'),\n"
        "          fill: true,\n"
        "          tension: 0.2,\n"
        "          pointRadius: 2,\n"
        "          borderWidth: 2\n"
        "        }]\n"
        "      },\n"
        "      options: {\n"
        "        responsive: true,\n"
        "        maintainAspectRatio: false,\n"
        "        plugins: {\n"
        "          legend: { display: true },\n"
        "          tooltip: { mode: 'index', intersect: false }\n"
        "        },\n"
        "        scales: {\n"
        "          x: { ticks: { maxTicksLimit: 12, autoSkip: true } },\n"
        "          y: { beginAtZero: false }\n"
        "        }\n"
        "      }\n"
        "    });\n"
        "  });\n"
        "});\n"
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )

    # 支援環境變數 OUTPUT_PATH 指定輸出位置（供 CI / GitHub Actions 使用），
    # 未設定時維持原本行為，輸出到目前目錄下的 rate_report.html。
    out_file = os.environ.get("OUTPUT_PATH", "rate_report.html")
    out_dir = os.path.dirname(out_file)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 報表已輸出：{out_file}")

    # 自動在預設瀏覽器開啟（CI 環境沒有瀏覽器，失敗時忽略即可，不影響報表產生）
    if not os.environ.get("CI"):
        try:
            webbrowser.open(out_file)
        except Exception:
            pass


def main():
    results = []

    # 先備妥 Chart.js（首次下載並快取）
    chart_js_content = ensure_chart_js()

    fetcher = RateFetcher()
    fetcher.start()
    try:
        for cur in CURRENCIES:
            dates, values = fetch_from_this_year(fetcher, cur)
            if not dates:
                print(f"{cur}: 從 {THIS_YEAR}/01/01 起無法取得資料")
                results.append({
                    "currency": cur,
                    "warn": False,
                    "detail": None,
                    "dates": [],
                    "values": [],
                    "last_date": None
                })
                continue

            warn, detail = check_rule(values)
            last_date_str = dates[-1] if dates else None

            results.append({
                "currency": cur,
                "warn": warn,
                "detail": detail,
                "dates": dates,
                "values": values,
                "last_date": last_date_str
            })
    finally:
        fetcher.stop()

    # 終端機輸出結果
    print(f"\n=== {THIS_YEAR} 年 1 月 1 日起判斷結果（現金匯率－本行賣出） ===")
    for r in results:
        cur = r["currency"]
        name_zh = NAMES_ZH.get(cur, cur)
        print(f"\n[{name_zh} ({cur})]")
        if not r["detail"]:
            print("  無資料")
            continue
        d = r["detail"]
        print(f"  平均值       : {d['avg']:.4f}")
        print(f"  當年最低值   : {d['min']:.4f}")
        print(f"  當年最高值   : {d['max']:.4f}")
        print(f"  今日掛牌值   : {d['today']:.4f}")
        print(f"  (平均值 - 最低值) / 3 + 最低值: {d['cond']:.4f}")
        if r["warn"]:
            print("  >>> 警示：(平均值 - 當年最低值) / 3 + 當年最低值 > 今日掛牌值，請注意！")
        else:
            print("  條件未達成。")

    # 產生自包含 HTML 報表並自動開啟
    generate_html(results, chart_js_content)


if __name__ == "__main__":
    main()
