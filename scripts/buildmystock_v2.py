#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Build docs/output.html for GitHub Pages.

The script reads stock codes from watch.txt, fetches the last N months of TWSE
price and foreign investor data, computes buy/sell observation points, and
embeds the resulting data directly into a static HTML page.

No historical CSV files are written.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


ROOT = Path(__file__).resolve().parents[1]
WATCH_FILE = ROOT / "watch.txt"
OUTPUT_HTML = ROOT / "docs" / "twse_buildmystock_v2.html"
CACHE_DIR = ROOT / ".cache" / "twse"

TWSE_FOREIGN_URL = "https://www.twse.com.tw/rwd/zh/fund/TWT38U"
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
TIMEZONE = ZoneInfo("Asia/Taipei")


@dataclass
class DailyRow:
    date: date
    stock_no: str
    stock_name: str
    close: float
    volume: int
    foreign_buy: int = 0
    foreign_sell: int = 0
    foreign_net: int = 0
    foreign_missing: bool = False
    ma5: float | None = None
    ma20: float | None = None
    foreign_net_5d: int | None = None
    foreign_net_20d: int | None = None
    foreign_cum_net: int = 0
    foreign_cum_net_5d_change: int | None = None
    signal: str = ""
    signal_reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build TWSE stock GitHub Pages HTML.")
    parser.add_argument("--months", type=int, default=6, help="Lookback months. Default: 6")
    parser.add_argument(
        "--end-date",
        default=datetime.now(TIMEZONE).date().isoformat(),
        help="End date in YYYY-MM-DD. Default: today in Asia/Taipei",
    )
    parser.add_argument("--watch", default=str(WATCH_FILE), help="Path to watch.txt")
    parser.add_argument("--output", default=str(OUTPUT_HTML), help="Path to output HTML")
    parser.add_argument("--sleep", type=float, default=0.45, help="Delay between API calls")
    parser.add_argument("--refresh", action="store_true", help="Ignore local JSON cache")
    return parser.parse_args()


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    days = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ]
    return date(year, month, min(d.day, days[month - 1]))


def month_starts(start: date, end: date) -> list[date]:
    current = date(start.year, start.month, 1)
    months: list[date] = []
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def parse_roc_date(value: str) -> date:
    year, month, day = [int(part) for part in value.strip().split("/")]
    return date(year + 1911, month, day)


def parse_int(value: Any) -> int:
    text = str(value or "").strip().replace(",", "")
    if text in {"", "--", "-"}:
        return 0
    return int(float(text))


def parse_float(value: Any) -> float:
    text = str(value or "").strip().replace(",", "").replace("+", "").replace("X", "")
    if text in {"", "--", "-"}:
        return math.nan
    return float(text)


def read_watch_codes(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"watch file not found: {path}")

    codes: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for token in re.split(r"[\s,，]+", line):
            code = token.strip()
            if re.fullmatch(r"\d{4,6}", code) and code not in seen:
                codes.append(code)
                seen.add(code)
    if not codes:
        raise ValueError(f"No valid stock codes were found in {path}")
    return codes


def fetch_json(
    url: str,
    params: dict[str, str],
    cache_path: Path,
    refresh: bool,
    sleep_seconds: float,
) -> dict[str, Any]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.twse.com.tw/",
    }
    last_error: Exception | None = None
    session = requests.Session()
    session.headers.update(headers)
    for attempt in range(1, 6):
        try:
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
            return payload
        except (
            requests.RequestException,
            json.JSONDecodeError,
            ValueError,
            OSError,
        ) as exc:
            last_error = exc
            time.sleep(2.0 * attempt)
    raise RuntimeError(f"Failed to fetch {url} params={params}: {last_error}") from last_error


def fetch_prices(
    stock_no: str,
    start: date,
    end: date,
    refresh: bool,
    sleep_seconds: float,
) -> list[DailyRow]:
    rows: list[DailyRow] = []
    stock_name = ""
    today_str = date.today().strftime("%Y%m%d")
    current_month_str = date.today().strftime("%Y%m")

    for month_start in month_starts(start, end):
        date_str = month_start.strftime("%Y%m%d")
        month_str = month_start.strftime("%Y%m")
        # 如果是當前月份，快取檔名加上今天日期，避免讀到舊的殘缺快取
        if month_str == current_month_str:
            cache_name = f"stock_day_{stock_no}_{date_str}_asof_{today_str}.json"
        else:
            cache_name = f"stock_day_{stock_no}_{date_str}.json"
        payload = fetch_json(
                TWSE_STOCK_DAY_URL,
                {"response": "json", "date": date_str, "stockNo": stock_no},
                CACHE_DIR / cache_name,
                refresh,
                sleep_seconds,
        )
        if payload.get("stat") != "OK":
            continue

        title = str(payload.get("title", ""))
        if stock_no in title:
            stock_name = title.split(stock_no, 1)[-1].split("各日成交資訊", 1)[0].strip()

        for item in payload.get("data", []):
            row_date = parse_roc_date(item[0])
            if start <= row_date <= end:
                rows.append(
                    DailyRow(
                        date=row_date,
                        stock_no=stock_no,
                        stock_name=stock_name,
                        close=parse_float(item[6]),
                        volume=parse_int(item[1]),
                    )
                )

    unique = {row.date: row for row in rows}
    ordered = [unique[d] for d in sorted(unique)]
    if not ordered:
        raise RuntimeError(f"No price data for {stock_no} between {start} and {end}")
    return ordered


def fetch_foreign_for_dates(
    stock_no: str,
    rows: list[DailyRow],
    refresh: bool,
    sleep_seconds: float,
) -> None:
    for row in rows:
        date_str = row.date.strftime("%Y%m%d")
        try:
            payload = fetch_json(
                TWSE_FOREIGN_URL,
                {"response": "json", "date": date_str, "selectType": "ALL"},
                CACHE_DIR / f"foreign_all_{date_str}.json",
                refresh,
                sleep_seconds,
            )
        except RuntimeError as exc:
            print(f"Warning: missing foreign data for {stock_no} {date_str}: {exc}")
            row.foreign_missing = True
            continue

        if payload.get("stat") != "OK":
            row.foreign_missing = True
            continue

        target = next(
            (item for item in payload.get("data", []) if str(item[1]).strip() == stock_no),
            None,
        )
        if target is None:
            row.foreign_missing = True
            continue

        if len(target) >= 12:
            # 12 欄位格式：合計值位於索引 9, 10, 11
            row.foreign_buy = parse_int(target[9])
            row.foreign_sell = parse_int(target[10])
            row.foreign_net = parse_int(target[11])
        elif len(target) >= 5:
            # 5 欄位格式：買進、賣出、買賣超
            # 加上 try-except 防禦機制，避免文字（如 '國巨*'）導致程式崩潰
            try:
                row.foreign_buy = parse_int(target[2])
                row.foreign_sell = parse_int(target[3])
                row.foreign_net = parse_int(target[4])
            except ValueError:
                # 如果抓到非數字欄位，設定為 0 並標記為資料缺失
                row.foreign_buy = 0
                row.foreign_sell = 0
                row.foreign_net = 0
                row.foreign_missing = True
        else:
            row.foreign_missing = True


def rolling_sum(values: list[int], index: int, window: int, min_periods: int) -> int | None:
    start = max(0, index - window + 1)
    subset = values[start : index + 1]
    if len(subset) < min_periods:
        return None
    return sum(subset)


def rolling_mean(values: list[float], index: int, window: int, min_periods: int) -> float | None:
    start = max(0, index - window + 1)
    subset = [v for v in values[start : index + 1] if not math.isnan(v)]
    if len(subset) < min_periods:
        return None
    return sum(subset) / len(subset)


def add_indicators(rows: list[DailyRow]) -> None:
    closes = [row.close for row in rows]
    foreign_nets = [row.foreign_net for row in rows]
    cumulative = 0
    cumulative_values: list[int] = []

    for index, row in enumerate(rows):
        cumulative += row.foreign_net
        cumulative_values.append(cumulative)
        row.ma5 = rolling_mean(closes, index, 5, 3)
        row.ma20 = rolling_mean(closes, index, 20, 8)
        row.foreign_net_5d = rolling_sum(foreign_nets, index, 5, 3)
        row.foreign_net_20d = rolling_sum(foreign_nets, index, 20, 8)
        row.foreign_cum_net = cumulative
        if index >= 5:
            row.foreign_cum_net_5d_change = cumulative - cumulative_values[index - 5]


def above(value: float, base: float | None) -> bool:
    return base is not None and not math.isnan(value) and value > base


def below(value: float, base: float | None) -> bool:
    return base is not None and not math.isnan(value) and value < base


def classify_signals(rows: list[DailyRow]) -> None:
    previous_zone = "NEUTRAL"
    last_action = ""

    for index, row in enumerate(rows):
        previous = rows[index - 1] if index > 0 else None
        buy_score = 0
        sell_score = 0

        if row.foreign_net_5d is not None and row.foreign_net_5d > 0:
            buy_score += 1
        if row.foreign_net_20d is not None and row.foreign_net_20d > 0:
            buy_score += 1
        if row.foreign_cum_net_5d_change is not None and row.foreign_cum_net_5d_change > 0:
            buy_score += 1
        if above(row.close, row.ma5):
            buy_score += 1
        if above(row.close, row.ma20):
            buy_score += 1

        if row.foreign_net_5d is not None and row.foreign_net_5d < 0:
            sell_score += 1
        if row.foreign_net_20d is not None and row.foreign_net_20d < 0:
            sell_score += 1
        if row.foreign_cum_net_5d_change is not None and row.foreign_cum_net_5d_change < 0:
            sell_score += 1
        if below(row.close, row.ma5):
            sell_score += 1
        if below(row.close, row.ma20):
            sell_score += 1

        if previous and previous.ma20 is not None and row.ma20 is not None:
            if previous.close <= previous.ma20 and row.close > row.ma20:
                buy_score += 1
            if previous.close >= previous.ma20 and row.close < row.ma20:
                sell_score += 1

        zone = "NEUTRAL"
        if buy_score >= 4 and buy_score > sell_score:
            zone = "BUY_ZONE"
        elif sell_score >= 4 and sell_score > buy_score:
            zone = "SELL_ZONE"

        if zone == "BUY_ZONE" and previous_zone != "BUY_ZONE" and last_action != "BUY":
            row.signal = "BUY"
            row.signal_reason = "；".join(
                [
                    f"外資5日買賣超 {to_lot(row.foreign_net_5d):,.0f} 張",
                    f"20日買賣超 {to_lot(row.foreign_net_20d):,.0f} 張",
                    "股價站上短中期均線" if above(row.close, row.ma20) else "股價轉強",
                ]
            )
            last_action = "BUY"
        elif zone == "SELL_ZONE" and previous_zone != "SELL_ZONE" and last_action != "SELL":
            row.signal = "SELL"
            row.signal_reason = "；".join(
                [
                    f"外資5日買賣超 {to_lot(row.foreign_net_5d):,.0f} 張",
                    f"20日買賣超 {to_lot(row.foreign_net_20d):,.0f} 張",
                    "股價跌破短中期均線" if below(row.close, row.ma20) else "股價轉弱",
                ]
            )
            last_action = "SELL"

        previous_zone = zone


def to_lot(value: int | None) -> float:
    return (value or 0) / 1000


def pct_change(first: float, last: float) -> float:
    if first == 0 or math.isnan(first) or math.isnan(last):
        return math.nan
    return (last / first - 1) * 100


def corr(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if not math.isnan(x) and not math.isnan(y)]
    if len(pairs) < 3:
        return None
    left = [x for x, _ in pairs]
    right = [y for _, y in pairs]
    try:
        return statistics.correlation(left, right)
    except statistics.StatisticsError:
        return None


def build_payload(rows: list[DailyRow]) -> dict[str, Any]:
    first = rows[0]
    latest = rows[-1]
    closes = [row.close for row in rows]
    daily_returns = [math.nan]
    for index in range(1, len(rows)):
        daily_returns.append(pct_change(rows[index - 1].close, rows[index].close))
    same_day_corr = corr([float(row.foreign_net) for row in rows], daily_returns)

    signals = [
        {
            "date": row.date.isoformat(),
            "signal": row.signal,
            "label": "買入觀察" if row.signal == "BUY" else "賣出觀察",
            "close": row.close,
            "foreignNet5Lot": round(to_lot(row.foreign_net_5d), 3),
            "foreignNet20Lot": round(to_lot(row.foreign_net_20d), 3),
            "reason": row.signal_reason,
        }
        for row in rows
        if row.signal
    ]

    return {
        "stockNo": latest.stock_no,
        "stockName": latest.stock_name or latest.stock_no,
        "startDate": first.date.isoformat(),
        "endDate": latest.date.isoformat(),
        "generatedAt": datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
        "latestClose": latest.close,
        "priceChangePct": pct_change(first.close, latest.close),
        "foreignCumNetLot": round(to_lot(latest.foreign_cum_net), 3),
        "sameDayCorrelation": same_day_corr,
        "missingForeignDays": sum(1 for row in rows if row.foreign_missing),
        "signals": signals,
        "series": [
            {
                "date": row.date.isoformat(),
                "close": row.close,
                "ma20": round(row.ma20, 3) if row.ma20 is not None else None,
                "foreignCumNetLot": round(to_lot(row.foreign_cum_net), 3),
                "foreignNetLot": round(to_lot(row.foreign_net), 3),
                "signal": row.signal,
            }
            for row in rows
        ],
        "rules": [
            "買入觀察：外資5日與20日買賣超轉強、累積買賣超上升，且股價站上短中期均線。",
            "賣出觀察：外資5日與20日買賣超轉弱、累積買賣超下降，且股價跌破短中期均線。",
            "訊號採買賣交替列示，避免同一段趨勢中重複列出多個同方向觀察點。",
        ],
    }


def fmt_number(value: float | None, digits: int = 2) -> str:
    if value is None or math.isnan(value):
        return "-"
    return f"{value:,.{digits}f}"


def build_html(stocks: dict[str, dict[str, Any]], months: int) -> str:
    payload = json.dumps(stocks, ensure_ascii=False, separators=(",", ":"))
    default_code = next(iter(stocks), "")
    title = "關注股票外資買賣超觀察"

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #697586;
      --line: #d9dee7;
      --blue: #2563a8;
      --red: #c7352f;
      --green: #218249;
      --shadow: 0 10px 30px rgba(31, 41, 51, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft JhengHei", "Noto Sans TC", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 42px;
    }}
    header {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(24px, 4vw, 36px);
      line-height: 1.2;
    }}
    .subtle {{ color: var(--muted); font-size: 14px; }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
    }}
    label {{ color: var(--muted); font-size: 14px; }}
    select {{
      min-width: 170px;
      border: 1px solid #b8c0cc;
      border-radius: 6px;
      padding: 9px 34px 9px 10px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .stat, .chart-panel, .signals, .rules {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .stat {{ padding: 14px 16px; min-height: 86px; }}
    .stat .label {{ color: var(--muted); font-size: 13px; }}
    .stat .value {{ margin-top: 8px; font-size: 24px; font-weight: 700; }}
    .chart-panel {{ padding: 20px; }}
    .chart-container {{
      position: relative;
      width: 100%;
      height: 520px;
    }}
    .signals, .rules {{
      margin-top: 14px;
      padding: 16px;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{ color: var(--muted); font-weight: 600; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-width: 76px;
      justify-content: center;
      border-radius: 999px;
      padding: 4px 8px;
      color: #fff;
      font-size: 12px;
      font-weight: 700;
    }}
    .badge.buy {{ background: var(--green); }}
    .badge.sell {{ background: var(--red); }}
    .rules ul {{ margin: 0; padding-left: 20px; color: var(--muted); line-height: 1.8; }}
    @media (max-width: 820px) {{
      header {{ align-items: stretch; flex-direction: column; }}
      .toolbar {{ justify-content: space-between; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .chart-container {{ height: 390px; }}
      table {{ font-size: 13px; }}
      th:nth-child(5), td:nth-child(5) {{ display: none; }}
    }}
    @media (max-width: 520px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 18px; }}
      .stats {{ grid-template-columns: 1fr; }}
      .toolbar {{ flex-direction: column; align-items: stretch; }}
      select {{ width: 100%; }}
      .chart-container {{ height: 330px; }}
      th:nth-child(4), td:nth-child(4) {{ display: none; }}
    }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js"></script>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{html.escape(title)}</h1>
        <div class="subtle">近 {months} 個月股價、20日均線與外資當日買賣超。資料來源：TWSE。</div>
      </div>
      <div class="toolbar">
        <label for="stockSelect">股票代號</label>
        <select id="stockSelect" aria-label="選擇股票代號"></select>
      </div>
    </header>

    <section class="stats" aria-label="摘要">
      <div class="stat"><div class="label">最新收盤價</div><div class="value" id="latestClose">-</div></div>
      <div class="stat"><div class="label">期間股價變化</div><div class="value" id="priceChange">-</div></div>
      <div class="stat"><div class="label">外資累積買賣超 (5日累計)</div><div class="value" id="foreignCum">-</div></div>
      <div class="stat"><div class="label">同日相關係數</div><div class="value" id="correlation">-</div></div>
    </section>

    <section class="chart-panel" aria-label="線圖">
      <div class="chart-container">
        <canvas id="stockChart"></canvas>
      </div>
    </section>

    <section class="signals">
      <h2 id="signalTitle">買入 / 賣出時間說明</h2>
      <div id="signalTable"></div>
    </section>

    <section class="rules">
      <h2>判斷規則</h2>
      <ul id="rules"></ul>
    </section>
  </main>

  <script id="stock-data" type="application/json">{payload}</script>
  <script>
    const STOCKS = JSON.parse(document.getElementById("stock-data").textContent);
    const select = document.getElementById("stockSelect");
    let chartInstance = null;

    const colors = {{
      blue: "#2563a8",
      red: "#c7352f",
      green: "#218249",
      gray: "#8b95a1",
      grid: "#e7ebf0",
      text: "#1f2933",
      muted: "#697586"
    }};

    const crosshairPlugin = {{
      id: 'crosshair',
      afterDatasetsDraw(chart, args, options) {{
        if (chart.tooltip?._active?.length) {{
          const activePoint = chart.tooltip._active[0];
          const {{ ctx, chartArea: {{ top, bottom, left, right }} }} = chart;
          const x = activePoint.element.x;
          const y = activePoint.element.y;

          ctx.save();
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 4]);
          ctx.strokeStyle = colors.muted;

          // 垂直線
          ctx.beginPath();
          ctx.moveTo(x, top);
          ctx.lineTo(x, bottom);
          ctx.stroke();

          // 水平線
          ctx.beginPath();
          ctx.moveTo(left, y);
          ctx.lineTo(right, y);
          ctx.stroke();
          ctx.restore();
        }}
      }}
    }};

    function formatNumber(value, digits = 2) {{
      if (value === null || value === undefined || Number.isNaN(value)) return "-";
      return Number(value).toLocaleString("zh-TW", {{
        minimumFractionDigits: digits,
        maximumFractionDigits: digits
      }});
    }}

    function formatPct(value) {{
      if (value === null || value === undefined || Number.isNaN(value)) return "-";
      return `${{formatNumber(value, 2)}}%`;
    }}

    function drawChart(stock) {{
      const data = stock.series;
      const labels = data.map(d => d.date);
      
      const closePrices = data.map(d => d.close);
      const ma20Values = data.map(d => d.ma20);
      const foreignNetLot = data.map(d => d.foreignNetLot); // 線圖改用當日買賣超

      const pointStyles = data.map(d => {{
        if (d.signal === "BUY") return "triangle";
        if (d.signal === "SELL") return "rectRot";
        return "circle";
      }});

      const pointColors = data.map(d => {{
        if (d.signal === "BUY") return colors.green;
        if (d.signal === "SELL") return colors.red;
        return colors.blue;
      }});

      const pointRadius = data.map(d => d.signal ? 8 : 0);
      const pointHoverRadius = data.map(d => d.signal ? 10 : 4);

      if (chartInstance) {{
        chartInstance.destroy();
      }}

      const ctx = document.getElementById("stockChart").getContext("2d");
      chartInstance = new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: labels,
          datasets: [
            {{
              label: '收盤價',
              data: closePrices,
              borderColor: colors.blue,
              backgroundColor: colors.blue,
              borderWidth: 2,
              yAxisID: 'yPrice',
              pointStyle: pointStyles,
              pointBackgroundColor: pointColors,
              pointBorderColor: pointColors,
              radius: pointRadius,
              hoverRadius: pointHoverRadius
            }},
            {{
              label: '20日均線',
              data: ma20Values,
              borderColor: colors.gray,
              borderWidth: 1.5,
              borderDash: [5, 5],
              pointStyle: 'none',
              pointRadius: 0,
              yAxisID: 'yPrice',
              fill: false
            }},
            {{
              label: '外資當日買賣超(張)', // 標籤與數據皆改為當日
              data: foreignNetLot,
              borderColor: colors.red,
              backgroundColor: colors.red,
              borderWidth: 1.5,
              yAxisID: 'yFlow',
              pointRadius: 0,
              hoverRadius: 0
            }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{
            mode: 'index',
            intersect: false
          }},
          plugins: {{
            legend: {{
              labels: {{
                font: {{ family: 'Microsoft JhengHei', size: 13 }}
              }}
            }},
            tooltip: {{
              backgroundColor: 'rgba(255, 255, 255, 0.95)',
              titleColor: colors.text,
              bodyColor: colors.text,
              borderColor: colors.line,
              borderWidth: 1,
              padding: 12,
              boxPadding: 6,
              titleFont: {{ family: 'Microsoft JhengHei', size: 14, weight: 'bold' }},
              bodyFont: {{ family: 'Microsoft JhengHei', size: 13 }},
              callbacks: {{
                label: function(context) {{
                  let label = context.dataset.label || '';
                  if (label) label += ': ';
                  if (context.parsed.y !== null) {{
                    label += formatNumber(context.parsed.y, context.datasetIndex === 2 ? 0 : 2);
                  }}
                  return label;
                }},
                footer: function(tooltipItems) {{
                  const index = tooltipItems[0].dataIndex;
                  const item = data[index];
                  // 游標移入時，在 Tooltip 底部同樣可以清晰呈現該日的訊號狀態
                  let footerText = "";
                  if (item.signal) {{
                    footerText += `提示訊號: ${{item.signal === "BUY" ? "【買入觀察】" : "【賣出觀察】"}}`;
                  }}
                  return footerText;
                }}
              }},
              footerColor: colors.text,
              footerFont: {{ family: 'Microsoft JhengHei', size: 13, weight: 'bold' }}
            }},
            zoom: {{
              pan: {{
                enabled: true,
                mode: 'x',
              }},
              zoom: {{
                wheel: {{
                  enabled: true,
                }},
                pinch: {{
                  enabled: true
                }},
                mode: 'x',
              }}
            }}
          }},
          scales: {{
            x: {{
              grid: {{ color: colors.grid }},
              ticks: {{
                font: {{ family: 'Microsoft JhengHei', size: 11 }},
                maxRotation: 45,
                minRotation: 0
              }}
            }},
            yPrice: {{
              type: 'linear',
              position: 'left',
              title: {{
                display: true,
                text: '股價 (及20日均線)',
                font: {{ family: 'Microsoft JhengHei', size: 13, weight: 'bold' }}
              }},
              grid: {{ color: colors.grid }},
              ticks: {{ font: {{ family: 'Arial', size: 12 }} }}
            }},
            yFlow: {{
              type: 'linear',
              position: 'right',
              title: {{
                display: true,
                text: '外資當日買賣超 (張)',
                font: {{ family: 'Microsoft JhengHei', size: 13, weight: 'bold' }}
              }},
              grid: {{ drawOnChartArea: false }},
              ticks: {{ font: {{ family: 'Arial', size: 12 }} }}
            }}
          }}
        }},
        plugins: [crosshairPlugin]
      }});
    }}

    function renderSignals(stock) {{
      document.getElementById("signalTitle").textContent =
        `${{stock.stockNo}} ${{stock.stockName}} 買入 / 賣出時間說明`;
      const target = document.getElementById("signalTable");
      if (!stock.signals.length) {{
        target.innerHTML = "<p class=\\"subtle\\">本期間沒有符合規則的買入或賣出觀察點。</p>";
        return;
      }}
      const rows = stock.signals.map((signal) => `
        <tr>
          <td>${{signal.date}}</td>
          <td><span class="badge ${{signal.signal === "BUY" ? "buy" : "sell"}}">${{signal.label}}</span></td>
          <td class="num">${{formatNumber(signal.close, 2)}}</td>
          <td class="num">${{formatNumber(signal.foreignNet5Lot, 0)}}</td>
          <td class="num">${{formatNumber(signal.foreignNet20Lot, 0)}}</td>
          <td>${{signal.reason}}</td>
        </tr>
      `).join("");
      target.innerHTML = `
        <table>
          <thead>
            <tr>
              <th>日期</th><th>訊號</th><th class="num">收盤價</th>
              <th class="num">外資5日(張)</th><th class="num">外資20日(張)</th><th>說明</th>
            </tr>
          </thead>
          <tbody>${{rows}}</tbody>
        </table>
      `;
    }}

    function renderRules(stock) {{
      document.getElementById("rules").innerHTML = stock.rules.map((rule) => `<li>${{rule}}</li>`).join("");
    }}

    function renderStock(code) {{
      const stock = STOCKS[code];
      if (!stock) return;
      document.getElementById("latestClose").textContent = formatNumber(stock.latestClose, 2);
      document.getElementById("priceChange").textContent = formatPct(stock.priceChangePct);
      
      // 最上方的外資區塊改為撈取最新一筆訊號或最後一天的「5日累計買賣超股數」
      // 由於後端傳過來的 signals 帶有 foreignNet5Lot，我們可以直接拿最後一天的 series 資料來用
      const latestSeries = stock.series[stock.series.length - 1];
      // 後端 payload 計算中，我們需要確保前端可以拿到 5 日累積數值。
      // 或者是直接從後端傳過來的最後一個符合訊號點拿，此處從後端 signals 陣列最後一個元素或預設判斷：
      const latest5dVol = stock.signals.length > 0 ? stock.signals[stock.signals.length - 1].foreignNet5Lot : 0;
      
      document.getElementById("foreignCum").textContent = `${{formatNumber(latest5dVol, 0)}} 張`;
      document.getElementById("correlation").textContent =
        stock.sameDayCorrelation === null ? "-" : formatNumber(stock.sameDayCorrelation, 3);
      drawChart(stock);
      renderSignals(stock);
      renderRules(stock);
    }}

    Object.values(STOCKS).forEach((stock) => {{
      const option = document.createElement("option");
      option.value = stock.stockNo;
      option.textContent = `${{stock.stockNo}} ${{stock.stockName}}`;
      select.appendChild(option);
    }});
    select.value = "{html.escape(default_code)}";
    select.addEventListener("change", () => renderStock(select.value));
    renderStock(select.value);
  </script>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    watch_file = Path(args.watch).resolve()
    output_html = Path(args.output).resolve()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    start = add_months(end, -args.months)

    codes = read_watch_codes(watch_file)
    print(f"Building {output_html} for {len(codes)} stock(s): {', '.join(codes)}")
    stocks: dict[str, dict[str, Any]] = {}

    for code in codes:
        print(f"Fetching {code}: {start} to {end}")
        rows = fetch_prices(code, start, end, args.refresh, args.sleep)
        fetch_foreign_for_dates(code, rows, args.refresh, args.sleep)
        add_indicators(rows)
        classify_signals(rows)
        stocks[code] = build_payload(rows)

    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(build_html(stocks, args.months), encoding="utf-8")
    print(f"Wrote {output_html}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
