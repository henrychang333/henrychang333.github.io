import os
import webbrowser
from datetime import datetime, date

import matplotlib.pyplot as plt
import twder

# === 參數設定 ===

CURRENCIES = ["AUD", "JPY", "USD", "EUR", "SGD"]

NAMES_ZH = {
    "USD": "美元",
    "EUR": "歐元",
    "AUD": "澳幣",
    "SGD": "新加坡幣",
    "JPY": "日圓",
}

COLORS = {
    "USD": "red",
    "EUR": "blue",
    "AUD": "green",
    "SGD": "orange",
    "JPY": "purple"
}

THIS_YEAR = date.today().year
START_DATE = date(THIS_YEAR, 1, 1)
TODAY_STR = date.today().strftime("%Y%m%d")   # 圖檔檔名用

# 台銀原始歷史匯率頁面（最近一年）網址格式
BASE_URL = "https://rate.bot.com.tw/xrt/quote/ltm/{}"

# Matplotlib 中文字型（依你的系統調整）
plt.rcParams["font.family"] = "Microsoft JhengHei"   # Windows
plt.rcParams["axes.unicode_minus"] = False

# 圖片輸出資料夾
OUT_DIR = "charts"
os.makedirs(OUT_DIR, exist_ok=True)


def fetch_from_this_year(cur):
    """
    從今年 1/1 起，用 twder 逐月抓取「現金賣出」資料。
    回傳 (dates, sell_cash)，日期由舊到新。
    """
    all_dates = []
    all_values = []

    today = date.today()
    for m in range(1, today.month + 1):
        data = twder.specify_month(cur, THIS_YEAR, m)
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
            all_dates.append(d)
            all_values.append(v)

    if not all_dates:
        return [], []
    all_dates, all_values = zip(*sorted(zip(all_dates, all_values)))
    return list(all_dates), list(all_values)


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


def plot_single(cur, dates, values, warn):
    """
    為單一幣別畫圖並存成 PNG，檔名包含年月日。
    """
    plt.figure(figsize=(10, 4))
    plt.plot(
        dates,
        values,
        color=COLORS.get(cur, "black"),
        label=f"{cur} 現金賣出"
    )
    plt.xlabel("日期")
    plt.ylabel("匯率")
    name_zh = NAMES_ZH.get(cur, cur)
    title = f"{name_zh}（{cur}） {THIS_YEAR} 年 1 月 1 日起現金匯率－本行賣出"
    if warn:
        title += "（警示條件成立）"
    plt.title(title)
    plt.legend()
    plt.grid(True)
    plt.tight_layout()

    filename = os.path.join(OUT_DIR, f"{cur}_{TODAY_STR}.png")
    plt.savefig(filename, dpi=120)
    plt.close()
    return filename


def generate_html(results):
    """
    產出 rate_report.html：
    - 表格：幣別(含連到台銀的超連結)、平均值、最低、最高、今日、條件值、警示。
    - 表格上方顯示今日掛牌值日期。
    - 下方顯示各幣別圖檔。
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

    # 各幣別圖
    img_blocks = []
    for r in results:
        cur = r["currency"]
        name_zh = NAMES_ZH.get(cur, cur)
        img_path = r.get("img_path")
        if not img_path:
            continue
        img_blocks.append(f"""
        <h3>{name_zh} ({cur})</h3>
        <img src="{img_path}" alt="{cur}" style="max-width: 100%; height: auto;">
        <hr>
        """)

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <title>{THIS_YEAR} 年匯率報表</title>
  <style>
    body {{ font-family: Arial, "Microsoft JhengHei", sans-serif; margin: 20px; }}
    table {{ border-collapse: collapse; margin-top: 20px; width: 50%; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: center; }}
    th {{ background: #f3f3f3; }}
  </style>
</head>
<body>
  <h2>{THIS_YEAR} 年 1 月 1 日起 台灣銀行現金匯率－本行賣出 報表</h2>

  <p>{date_text}</p>

  <h3>統計與警示條件（(平均值 - 當年最低值)/3 + 當年最低值 > 今日掛牌值）</h3>
  <table>
    <thead>
      <tr>
        <th>幣別</th>
        <th>平均值</th>
        <th>當年最低值</th>
        <th>當年最高值</th>
        <th>今日掛牌值</th>
        <th>(平均值 - 最低值) / 3 + 最低值</th>
        <th>警示</th>
      </tr>
    </thead>
    <tbody>
      {''.join(html_rows)}
    </tbody>
  </table>

  <h3>各幣別走勢圖</h3>
  {''.join(img_blocks)}

</body>
</html>
"""
    out_file = "rate_report.html"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML 報表已輸出：{out_file}")

    # 自動在預設瀏覽器開啟
    webbrowser.open(out_file)


def main():
    results = []

    for cur in CURRENCIES:
        dates, values = fetch_from_this_year(cur)
        if not dates:
            print(f"{cur}: 從 {THIS_YEAR}/01/01 起無法取得資料")
            results.append({
                "currency": cur,
                "warn": False,
                "detail": None,
                "img_path": None,
                "last_date": None
            })
            continue

        warn, detail = check_rule(values)
        img_path = plot_single(cur, dates, values, warn)
        last_date_str = dates[-1].strftime("%Y/%m/%d")

        results.append({
            "currency": cur,
            "warn": warn,
            "detail": detail,
            "img_path": img_path,
            "last_date": last_date_str
        })

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

    # 產生 HTML 報表並自動開啟
    generate_html(results)


if __name__ == "__main__":
    main()