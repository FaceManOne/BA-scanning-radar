import logging

import requests

from datetime import datetime


# ============================================================
# 工具函数
# ============================================================

def get_quote_volume(symbol):
    for url in (
        "https://fapi.binance.com/fapi/v1/ticker/24hr",
        "https://api.binance.com/api/v3/ticker/24hr",
    ):
        try:
            r = requests.get(url, params={"symbol": symbol}, timeout=5)
            data = r.json()
            qv = float(data.get("quoteVolume", 0))
            if qv > 0:
                return qv
        except Exception:
            continue
    return None


def format_volume(quote_volume):
    if quote_volume is None:
        return None
    millions = quote_volume / 1_000_000
    if millions < 0.1:
        return "<0.1M"
    return "{0:.1f}M".format(millions)


def format_price(price):
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "N/A"

    if p >= 1000:
        return "{0:.0f}".format(p)
    elif p >= 1:
        return "{0:.2f}".format(p)
    else:
        s = "{0:.8f}".format(p)
        s = s.rstrip("0").rstrip(".")
        if s == "":
            s = "0"
        return s


def strip_usdt(symbol):
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


class ReportGenerator:
    def __init__(
        self,
        telegram,
        alert_skip_threshold,
        alert_levels=None,
        min_quote_volume=None,
        stock_symbols=None,
        email_sender=None,
        pump_emoji="\U0001F7E2",
        dump_emoji="\U0001F534",
    ):
        self.telegram = telegram
        self.alert_skip_threshold = alert_skip_threshold
        self.pump_emoji = pump_emoji
        self.dump_emoji = dump_emoji

        if alert_levels is None:
            alert_levels = {
                "crypto": {"up": [0.025, 0.05, 0.075], "down": [0.025, 0.05, 0.075]},
                "tradfi": {"up": [0.015, 0.025, 0.035], "down": [0.015, 0.025, 0.035]},
            }
        self.alert_levels = alert_levels

        if min_quote_volume is None:
            min_quote_volume = {"crypto": 30_000_000, "tradfi": 9_000_000}
        self.min_quote_volume = min_quote_volume

        self.stock_symbols = stock_symbols or set()
        self.email_sender = email_sender

        self.recent_alerts_15m = []
        self.recent_alerts_1h = []

        self.logger = logging.getLogger("report-generator")

    # ============================================================
    # 分类工具
    # ============================================================

    def _category(self, symbol):
        if symbol in self.stock_symbols:
            return "tradfi"
        return "crypto"

    def _threshold(self, symbol):
        return self.min_quote_volume[self._category(symbol)]

    def _levels(self, symbol, direction):
        return self.alert_levels[self._category(symbol)][direction]

    # ============================================================
    # 单行格式化
    # ============================================================

    def _fmt_row(self, symbol, change, price, mark=""):
        """输出格式：币种 | 成交额  涨幅 | 💲价格  [火焰/雪花]"""
        qv = get_quote_volume(symbol)
        if qv is None or qv < self._threshold(symbol):
            return None
        qv_str = format_volume(qv)
        sign = "+" if change > 0 else ""
        price_str = format_price(price)

        base = "  {0} | {1}  {2}{3:.2f}% | \U0001F4B2{4}".format(
            strip_usdt(symbol), qv_str, sign, change * 100, price_str
        )
        if mark:
            base += "  " + mark
        return base

    # ============================================================
    # 即时警报
    # ============================================================

    def _build_alert_message(self, symbol, interval, change, price, emoji):
        qv = get_quote_volume(symbol)
        qv_str = format_volume(qv)

        if qv is None or qv < self._threshold(symbol):
            return None

        if change > 0:
            arrow = "\U0001F4C8"
            sign = "+"
        else:
            arrow = "\U0001F4C9"
            sign = ""

        price_str = format_price(price)

        msg = "{0} {1} | {2} | \U0001F30A{3} {4}{5}{6:.2f}% | \U0001F4B2{7}".format(
            emoji,
            strip_usdt(symbol),
            interval,
            qv_str,
            arrow,
            sign,
            change * 100,
            price_str,
        )
        return msg

    def send_pump_message(self, symbol, interval, change, price):
        msg = self._build_alert_message(
            symbol, interval, change, price, self.pump_emoji
        )
        if msg is None:
            return
        self.telegram.send_message(msg, is_alert_chat=False)

    def send_dump_message(self, symbol, interval, change, price):
        msg = self._build_alert_message(
            symbol, interval, change, price, self.dump_emoji
        )
        if msg is None:
            return
        self.telegram.send_message(msg, is_alert_chat=False)

    def send_new_listings(self, symbols_to_add):
        message = """\
*New Listings*
{0} new pairs found, adding to monitored list.

*Adding Pairs:*\
            """.format(len(symbols_to_add))

        message += "\n"
        for symbol in symbols_to_add:
            message += "- _{0}_\n".format(symbol)

        self.telegram.send_news_message(message, is_alert_chat=True)

    # ============================================================
    # 分档即时警报
    # ============================================================

    def send_pump_dump_message(
        self,
        asset,
        chart_intervals,
        outlier_intervals,
        current_time,
        dump_enabled=True,
    ):
        for interval in chart_intervals:
            change = asset[interval]["change_current"]

            # 涨侧
            target_up = 0
            up_levels = sorted(self._levels(asset["symbol"], "up"))
            for idx, lv in enumerate(up_levels):
                if change >= lv:
                    target_up = idx
                else:
                    break

            if target_up > asset.get("push_level_up", 0):
                price = asset["price"][-1]
                self.send_pump_message(asset["symbol"], interval, change, price)
                asset["push_level_up"] = target_up

                self.recent_alerts_15m.append({
                    "symbol": asset["symbol"],
                    "change": change,
                    "price": price,
                    "level": target_up,
                    "direction": "up",
                })
                self.recent_alerts_1h.append({
                    "symbol": asset["symbol"],
                    "change": change,
                    "price": price,
                    "level": target_up,
                    "direction": "up",
                })

            # 跌侧
            if not dump_enabled:
                continue

            target_down = 0
            down_levels = sorted(self._levels(asset["symbol"], "down"), reverse=True)
            for idx, lv in enumerate(down_levels):
                if change <= -lv:
                    target_down = idx
                else:
                    break

            if target_down > asset.get("push_level_down", 0):
                price = asset["price"][-1]
                self.send_dump_message(asset["symbol"], interval, change, price)
                asset["push_level_down"] = target_down

                self.recent_alerts_15m.append({
                    "symbol": asset["symbol"],
                    "change": change,
                    "price": price,
                    "level": target_down,
                    "direction": "down",
                })
                self.recent_alerts_1h.append({
                    "symbol": asset["symbol"],
                    "change": change,
                    "price": price,
                    "level": target_down,
                    "direction": "down",
                })

    # ============================================================
    # 统计
    # ============================================================

    def _market_statistics(self, assets, interval, category):
        up = 0
        down = 0
        sum_change = 0.0
        total = 0

        for asset in assets:
            if self._category(asset["symbol"]) != category:
                continue
            change = asset[interval]["change_current"]
            sum_change += change
            total += 1
            if change > 0:
                up += 1
            elif change < 0:
                down += 1

        if total == 0:
            return 0.0, 0, 0
        return sum_change / total, up, down

    def _market_statistics_1h(self, assets, one_hour_points, category):
        up = 0
        down = 0
        sum_change = 0.0
        total = 0

        for asset in assets:
            if self._category(asset["symbol"]) != category:
                continue
            ps = asset["price"]
            if len(ps) < one_hour_points:
                continue
            old = ps[-one_hour_points]
            if old == 0:
                continue
            change = (ps[-1] - old) / old
            sum_change += change
            total += 1
            if change > 0:
                up += 1
            elif change < 0:
                down += 1

        if total == 0:
            return 0.0, 0, 0
        return sum_change / total, up, down

    # ============================================================
    # 追加区块：曾触及高档位
    # ============================================================

    def _build_extra_block(self, alerts, min_level):
        seen = {}
        for a in alerts:
            if a["level"] < min_level:
                continue
            sym = a["symbol"]
            if sym not in seen or abs(a["change"]) > abs(seen[sym]["change"]):
                seen[sym] = a

        up_lines = []
        down_lines = []
        for sym, a in seen.items():
            if a["direction"] == "up":
                mark = "\U0001F525" * min_level
                line = self._fmt_row(sym, a["change"], a["price"], mark)
                if line is not None:
                    up_lines.append(line)
            else:
                mark = "\u2744\uFE0F" * min_level
                line = self._fmt_row(sym, a["change"], a["price"], mark)
                if line is not None:
                    down_lines.append(line)
        return up_lines, down_lines

    # ============================================================
    # 15分钟汇总
    # ============================================================

    def send_summary_report(self, assets, chart_intervals, summary_config):
        min_change = summary_config.get("min_change", 0.025)
        max_coins = summary_config.get("max_coins", 20)
        interval = list(chart_intervals.keys())[0]

        ups = []
        downs = []
        for asset in assets:
            change = asset[interval]["change_current"]
            cat = self._category(asset["symbol"])
            base = min(self.alert_levels[cat]["up"])
            if change >= base:
                ups.append((asset["symbol"], change, asset["price"][-1]))
            elif change <= -base:
                downs.append((asset["symbol"], change, asset["price"][-1]))

        ups.sort(key=lambda x: x[1], reverse=True)
        downs.sort(key=lambda x: x[1])

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = ["\U0001F4CA *15分钟汇总报告* | {0}".format(now_str), ""]

        up_lines = []
        for sym, chg, price in ups[:max_coins]:
            line = self._fmt_row(sym, chg, price)
            if line is not None:
                up_lines.append(line)

        down_lines = []
        for sym, chg, price in downs[:max_coins]:
            line = self._fmt_row(sym, chg, price)
            if line is not None:
                down_lines.append(line)

        extra_up, extra_down = self._build_extra_block(self.recent_alerts_15m, 1)

        lines.append("\U0001F4C8 *涨幅榜*")
        if up_lines:
            lines.extend(up_lines)
        else:
            lines.append("  （无）")
        if extra_up:
            lines.append("  " + "\u2501" * 35)
            lines.extend(extra_up)

        lines.append("")
        lines.append("\U0001F4C9 *跌幅榜*")
        if down_lines:
            lines.extend(down_lines)
        else:
            lines.append("  （无）")
        if extra_down:
            lines.append("  " + "\u2501" * 35)
            lines.extend(extra_down)

        avg_c, up_c, dn_c = self._market_statistics(assets, interval, "crypto")
        avg_t, up_t, dn_t = self._market_statistics(assets, interval, "tradfi")

        lines.append("")
        lines.append("\U0001F30A *市场潮汐*")
        lines.append("  Crypto Market Average: {0:+.2f}%".format(avg_c * 100))
        lines.append("  \U0001F7E2 {0} / \U0001F534 {1}".format(up_c, dn_c))
        lines.append("  TradFi Market Average: {0:+.2f}%".format(avg_t * 100))
        lines.append("  \U0001F7E2 {0} / \U0001F534 {1}".format(up_t, dn_t))

        self.telegram.send_report_message("\n".join(lines))

        if self.email_sender:
            self.email_sender.send("15分钟汇总报告 | {0}".format(now_str), "\n".join(lines))

        self.recent_alerts_15m = []

    # ============================================================
    # 1小时榜单
    # ============================================================

    def send_hourly_report(self, assets, chart_intervals, extract_interval, hourly_config):
        min_change = hourly_config.get("min_change", {"crypto": 0.10, "tradfi": 0.05})
        max_coins = hourly_config.get("max_coins", 10)
        interval = list(chart_intervals.keys())[0]

        one_hour_points = 3600 // extract_interval

        data_enough = False
        if assets:
            data_enough = len(assets[0]["price"]) >= one_hour_points

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        if not data_enough:
            lines = ["\u23F0 *1\u5c0f\u65f6\u699c\u5355* | {0}".format(now_str), ""]
            lines.append("\U0001F4CA 数据积累中...")
            self.telegram.send_report_message("\n".join(lines))
            return

        ups = []
        downs = []
        for asset in assets:
            ps = asset["price"]
            if len(ps) < one_hour_points:
                continue
            old = ps[-one_hour_points]
            if old == 0:
                continue
            change = (ps[-1] - old) / old
            cat = self._category(asset["symbol"])
            threshold = min_change[cat]
            if change >= threshold:
                ups.append((asset["symbol"], change, ps[-1]))
            elif change <= -threshold:
                downs.append((asset["symbol"], change, ps[-1]))

        ups.sort(key=lambda x: x[1], reverse=True)
        downs.sort(key=lambda x: x[1])

        up_lines = []
        for sym, chg, price in ups[:max_coins]:
            line = self._fmt_row(sym, chg, price)
            if line is not None:
                up_lines.append(line)

        down_lines = []
        for sym, chg, price in downs[:max_coins]:
            line = self._fmt_row(sym, chg, price)
            if line is not None:
                down_lines.append(line)

        extra_up, extra_down = self._build_extra_block(self.recent_alerts_1h, 2)

        lines = ["\u23F0 *1\u5c0f\u65f6\u699c\u5355* | {0}".format(now_str), ""]

        lines.append("\U0001F4C8 *1h 涨幅榜*")
        if up_lines:
            lines.extend(up_lines)
        else:
            lines.append("  （无）")
        if extra_up:
            lines.append("  " + "\u2501" * 35)
            lines.extend(extra_up)

        lines.append("")
        lines.append("\U0001F4C9 *1h 跌幅榜*")
        if down_lines:
            lines.extend(down_lines)
        else:
            lines.append("  （无）")
        if extra_down:
            lines.append("  " + "\u2501" * 35)
            lines.extend(extra_down)

        avg_c, up_c, dn_c = self._market_statistics_1h(assets, one_hour_points, "crypto")
        avg_t, up_t, dn_t = self._market_statistics_1h(assets, one_hour_points, "tradfi")

        lines.append("")
        lines.append("\U0001F30A *市场潮汐*")
        lines.append("  Crypto 1h Average: {0:+.2f}%".format(avg_c * 100))
        lines.append("  \U0001F7E2 {0} / \U0001F534 {1}".format(up_c, dn_c))
        lines.append("  TradFi 1h Average: {0:+.2f}%".format(avg_t * 100))
        lines.append("  \U0001F7E2 {0} / \U0001F534 {1}".format(up_t, dn_t))

        self.telegram.send_report_message("\n".join(lines))

        if self.email_sender:
            self.email_sender.send("1小时榜单 | {0}".format(now_str), "\n".join(lines))

        self.recent_alerts_1h = []

    # ============================================================
    # Top Pump & Dump（原版保留）
    # ============================================================

    def send_top_pump_dump_statistics_report(
        self, assets, interval,
        top_pump_enabled=True, top_dump_enabled=True,
        additional_stats_enabled=True, no_of_reported_coins=5,
    ):
        if not top_pump_enabled or not top_dump_enabled:
            return

        message = "*[{0} Interval]*\n\n".format(interval)

        if top_pump_enabled:
            lst = sorted(assets, key=lambda item: item[interval]["change_current"], reverse=True)[0:no_of_reported_coins]
            message += "{0} *Top {1} Pumps*\n".format(self.pump_emoji, no_of_reported_coins)
            for asset in lst:
                message += "- {0}: _{1:.2f}_%\n".format(strip_usdt(asset["symbol"]), asset[interval]["change_current"] * 100)
            message += "\n"

        if top_dump_enabled:
            lst = sorted(assets, key=lambda item: item[interval]["change_current"])[0:no_of_reported_coins]
            message += "{0} *Top {1} Dumps*\n".format(self.dump_emoji, no_of_reported_coins)
            for asset in lst:
                message += "- {0}: _{1:.2f}_%\n".format(strip_usdt(asset["symbol"]), asset[interval]["change_current"] * 100)

        if additional_stats_enabled:
            if top_pump_enabled or top_dump_enabled:
                message += "\n"
            message += self.generate_additional_statistics_report(assets, interval)

        self.telegram.send_report_message(message, is_alert_chat=True)

    def generate_additional_statistics_report(self, assets, interval):
        up = 0
        down = 0
        sum_change = 0
        for asset in assets:
            if asset[interval]["change_current"] > 0:
                up += 1
            elif asset[interval]["change_current"] < 0:
                down += 1
            sum_change += asset[interval]["change_current"]
        avg_change = sum_change / len(assets)
        return "*Average Change:* {0:.2f}%\n {1} {2} / {3} {4}".format(
            avg_change * 100, self.pump_emoji, up, self.dump_emoji, down,
        )