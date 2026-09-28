import logging

import requests

from datetime import datetime


# ============================================================
# 成交额查询与格式化工具
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
    """把成交额格式化为 X.XM 或 <0.1M。"""
    if quote_volume is None:
        return None
    millions = quote_volume / 1_000_000
    if millions < 0.1:
        return "<0.1M"
    return "{0:.1f}M".format(millions)


def format_price(price):
    """按规则格式化价格。"""
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
    """去掉 USDT 后缀。"""
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
        pump_emoji="\U0001F7E2",  # 🟢
        dump_emoji="\U0001F534",  # 🔴
    ):
        self.telegram = telegram
        self.alert_skip_threshold = alert_skip_threshold
        self.pump_emoji = pump_emoji
        self.dump_emoji = dump_emoji

        # 两套档位
        if alert_levels is None:
            alert_levels = {
                "crypto": {"up": [0.025, 0.05, 0.075], "down": [0.025, 0.05, 0.075]},
                "tradfi": {"up": [0.015, 0.03, 0.045], "down": [0.015, 0.03, 0.045]},
            }
        self.alert_levels = alert_levels

        # 两套门槛
        if min_quote_volume is None:
            min_quote_volume = {"crypto": 30_000_000, "tradfi": 9_000_000}
        self.min_quote_volume = min_quote_volume

        self.stock_symbols = stock_symbols or set()
        self.email_sender = email_sender

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

        # msg += "\n\nOpen in [Binance Spot](https://www.binance.com/en/trade/{0})".format(symbol)

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
    # 分档即时警报（按分类走不同档位）
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
            for lv in sorted(self._levels(asset["symbol"], "up")):
                if change >= lv:
                    target_up = int(lv * 1000)
                else:
                    break

            if target_up > asset.get("push_level_up", 0):
                price = asset["price"][-1]
                self.send_pump_message(asset["symbol"], interval, change, price)
                asset["push_level_up"] = target_up

            # 跌侧
            if not dump_enabled:
                continue

            target_down = 0
            for lv in sorted(self._levels(asset["symbol"], "down"), reverse=True):
                if change <= -lv:
                    target_down = int(lv * 1000)
                else:
                    break

            if target_down > asset.get("push_level_down", 0):
                price = asset["price"][-1]
                self.send_dump_message(asset["symbol"], interval, change, price)
                asset["push_level_down"] = target_down

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
            # 用该分类的最低档作为入榜门槛
            base = min(self.alert_levels[cat]["up"])
            if change >= base:
                ups.append((asset["symbol"], change))
            elif change <= -base:
                downs.append((asset["symbol"], change))

        ups.sort(key=lambda x: x[1], reverse=True)
        downs.sort(key=lambda x: x[1])

        def fmt_line(sym, chg):
            qv = get_quote_volume(sym)
            if qv is None or qv < self._threshold(sym):
                return None
            qv_str = format_volume(qv)
            sign = "+" if chg > 0 else ""
            return "  {0} {1}{2:.2f}%  |  {3}".format(strip_usdt(sym), sign, chg * 100, qv_str)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = ["\U0001F4CA *15分钟汇总报告* | {0}".format(now_str), ""]

        up_lines = []
        for sym, chg in ups[:max_coins]:
            line = fmt_line(sym, chg)
            if line is not None:
                up_lines.append(line)

        down_lines = []
        for sym, chg in downs[:max_coins]:
            line = fmt_line(sym, chg)
            if line is not None:
                down_lines.append(line)

        lines.append("\U0001F4C8 *涨幅榜*")
        if up_lines:
            lines.extend(up_lines)
        else:
            lines.append("  （无）")

        lines.append("")
        lines.append("\U0001F4C9 *跌幅榜*")
        if down_lines:
            lines.extend(down_lines)
        else:
            lines.append("  （无）")

        # 分类统计
        avg_c, up_c, dn_c = self._market_statistics(assets, interval, "crypto")
        avg_t, up_t, dn_t = self._market_statistics(assets, interval, "tradfi")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("\U0001F4CA Crypto Market Average: {0:+.2f}%".format(avg_c * 100))
        lines.append("\U0001F7E2 {0} / \U0001F534 {1}".format(up_c, dn_c))
        lines.append("")
        lines.append("\U0001F4CA TradFi Market Average: {0:+.2f}%".format(avg_t * 100))
        lines.append("\U0001F7E2 {0} / \U0001F534 {1}".format(up_t, dn_t))

        self.telegram.send_report_message("\n".join(lines))

        if self.email_sender:
            self.email_sender.send("15分钟汇总报告", "\n".join(lines))

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

        if not data_enough:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
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
                ups.append((asset["symbol"], change))
            elif change <= -threshold:
                downs.append((asset["symbol"], change))

        ups.sort(key=lambda x: x[1], reverse=True)
        downs.sort(key=lambda x: x[1])

        def fmt_line(sym, chg):
            qv = get_quote_volume(sym)
            if qv is None or qv < self._threshold(sym):
                return None
            qv_str = format_volume(qv)
            sign = "+" if chg > 0 else ""
            return "  {0} {1}{2:.2f}%  |  {3}".format(strip_usdt(sym), sign, chg * 100, qv_str)

        up_lines = []
        for sym, chg in ups[:max_coins]:
            line = fmt_line(sym, chg)
            if line is not None:
                up_lines.append(line)

        down_lines = []
        for sym, chg in downs[:max_coins]:
            line = fmt_line(sym, chg)
            if line is not None:
                down_lines.append(line)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = ["\u23F0 *1\u5c0f\u65f6\u699c\u5355* | {0}".format(now_str), ""]

        if up_lines:
            lines.append("\U0001F4C8 *1h \u6da8\u5e45\u699c*")
            lines.extend(up_lines)
            lines.append("")
        if down_lines:
            lines.append("\U0001F4C9 *1h \u8dcc\u5e45\u699c*")
            lines.extend(down_lines)

        avg_c, up_c, dn_c = self._market_statistics_1h(assets, one_hour_points, "crypto")
        avg_t, up_t, dn_t = self._market_statistics_1h(assets, one_hour_points, "tradfi")

        lines.append("")
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("\U0001F4CA Crypto 1h Average: {0:+.2f}%".format(avg_c * 100))
        lines.append("\U0001F7E2 {0} / \U0001F534 {1}".format(up_c, dn_c))
        lines.append("")
        lines.append("\U0001F4CA TradFi 1h Average: {0:+.2f}%".format(avg_t * 100))
        lines.append("\U0001F7E2 {0} / \U0001F534 {1}".format(up_t, dn_t))

        self.telegram.send_report_message("\n".join(lines))

        if self.email_sender:
            self.email_sender.send("1小时榜单", "\n".join(lines))

    # ============================================================
    # Top Pump & Dump（保留原版）
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