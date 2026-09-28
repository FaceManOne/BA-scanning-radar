import logging

import requests

from datetime import datetime


# ============================================================
# 成交额查询与格式化工具
# ============================================================

def get_quote_volume(symbol, api_url="https://api.binance.com/api/v3/ticker/24hr"):
    """查询单个交易对的24h成交额(USDT)。失败返回None。"""
    try:
        r = requests.get(api_url, params={"symbol": symbol}, timeout=5)
        data = r.json()
        return float(data["quoteVolume"])
    except Exception:
        return None


def format_volume(quote_volume):
    """把成交额格式化为 X.XM 或 <0.1M。"""
    if quote_volume is None:
        return None
    millions = quote_volume / 1_000_000
    if millions < 0.1:
        return "<0.1M"
    return "{0:.1f}M".format(millions)


class ReportGenerator:
    def __init__(
        self,
        telegram,
        alert_skip_threshold,
        alert_levels=None,
        min_quote_volume=10_000_000,
        pump_emoji="\U0001F7E2",  # 🟢
        dump_emoji="\U0001F534",  # 🔴
    ):
        self.telegram = telegram
        self.alert_skip_threshold = alert_skip_threshold
        self.pump_emoji = pump_emoji
        self.dump_emoji = dump_emoji

        if alert_levels is None:
            alert_levels = {
                "up": [0.05, 0.10, 0.15],
                "down": [0.05, 0.10, 0.15],
            }
        self.alert_levels = alert_levels
        self.min_quote_volume = min_quote_volume

        self.logger = logging.getLogger("report-generator")

    # ============================================================
    # 即时警报（涨/跌）
    # ============================================================

    def _build_alert_message(self, symbol, interval, change, price, emoji):
        # 查询成交额
        qv = get_quote_volume(symbol)
        qv_str = format_volume(qv)

        # 硬门槛：成交额不达标，直接返回 None，不推送
        if qv is None or qv < self.min_quote_volume:
            return None

        vol_block = "💰 24h Volume: {0} USDT".format(qv_str)

        return """\
{0} *{1} [{2} Interval]* | Change: _{3:.3f}%_

{4}

Open in [Binance Spot](https://www.binance.com/en/trade/{1})\
        """.format(
            emoji,
            symbol,
            interval,
            change * 100,
            vol_block,
        )

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

    # ============================================================
    # 新上市通知
    # ============================================================

    def send_new_listings(self, symbols_to_add):
        message = """\
*New Listings*
{0} new pairs found, adding to monitored list.

*Adding Pairs:*\
            """.format(
            len(symbols_to_add)
        )

        message += "\n"
        for symbol in symbols_to_add:
            message += "- _{0}_\n".format(symbol)

        self.telegram.send_news_message(message, is_alert_chat=True)

    # ============================================================
    # 分档即时警报（涨跌对称，档位只升不降）
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

            # ===== 涨侧分档 =====
            target_up = 0
            for lv in sorted(self.alert_levels.get("up", [])):
                if change >= lv:
                    target_up = int(lv * 100)
                else:
                    break

            if target_up > asset.get("push_level_up", 0):
                price = asset["price"][-1]
                self.send_pump_message(asset["symbol"], interval, change, price)
                asset["push_level_up"] = target_up

            # ===== 跌侧分档 =====
            if not dump_enabled:
                continue

            target_down = 0
            for lv in sorted(self.alert_levels.get("down", []), reverse=True):
                if change <= -lv:
                    target_down = int(lv * 100)
                else:
                    break

            if target_down > asset.get("push_level_down", 0):
                price = asset["price"][-1]
                self.send_dump_message(asset["symbol"], interval, change, price)
                asset["push_level_down"] = target_down

    # ============================================================
    # 15分钟汇总报告
    # ============================================================

    def send_summary_report(self, assets, chart_intervals, summary_config):
        min_change = summary_config.get("min_change", 0.05)
        max_coins = summary_config.get("max_coins", 20)
        interval = list(chart_intervals.keys())[0]

        ups = []
        downs = []
        for asset in assets:
            change = asset[interval]["change_current"]
            if change >= min_change:
                ups.append((asset["symbol"], change))
            elif change <= -min_change:
                downs.append((asset["symbol"], change))

        ups.sort(key=lambda x: x[1], reverse=True)
        downs.sort(key=lambda x: x[1])

        def fmt_line(sym, chg):
            qv = get_quote_volume(sym)
            if qv is None or qv < self.min_quote_volume:
                return None
            qv_str = format_volume(qv)
            sign = "+" if chg > 0 else ""
            return "  {0} {1}{2:.2f}%  |  {3}".format(sym, sign, chg * 100, qv_str)

        lines = ["\U0001F4CA *15分钟汇总报告*", ""]

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

        self.telegram.send_report_message("\n".join(lines))

    # ============================================================
    # 1小时榜单（独立，整点，有内容才发）
    # ============================================================

    def send_hourly_report(self, assets, chart_intervals, extract_interval, hourly_config):
        min_change = hourly_config.get("min_change", 0.10)
        max_coins = hourly_config.get("max_coins", 10)
        interval = list(chart_intervals.keys())[0]

        # 1小时需要的数据点数
        one_hour_points = 3600 // extract_interval

        ups = []
        downs = []
        for asset in assets:
            price_series = asset["price"]
            if len(price_series) < one_hour_points:
                continue  # 数据不够，跳过
            old_price = price_series[-one_hour_points]
            if old_price == 0:
                continue
            change = (price_series[-1] - old_price) / old_price
            if change >= min_change:
                ups.append((asset["symbol"], change))
            elif change <= -min_change:
                downs.append((asset["symbol"], change))

        # 没有内容就不发
        if not ups and not downs:
            return

        ups.sort(key=lambda x: x[1], reverse=True)
        downs.sort(key=lambda x: x[1])

        def fmt_line(sym, chg):
            qv = get_quote_volume(sym)
            if qv is None or qv < self.min_quote_volume:
                return None
            qv_str = format_volume(qv)
            sign = "+" if chg > 0 else ""
            return "  {0} {1}{2:.2f}%  |  {3}".format(sym, sign, chg * 100, qv_str)

        # 先过滤 None，再判断是否有内容
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

        # 成交额过滤后全空，就不发
        if not up_lines and not down_lines:
            return

        lines = ["\u23F0 *1\u5c0f\u65f6\u699c\u5355*", ""]

        if up_lines:
            lines.append("\U0001F4C8 *1h \u6da8\u5e45\u699c*")
            lines.extend(up_lines)
            lines.append("")

        if down_lines:
            lines.append("\U0001F4C9 *1h \u8dcc\u5e45\u699c*")
            lines.extend(down_lines)

        self.telegram.send_report_message("\n".join(lines))

    # ============================================================
    # Top Pump & Dump 统计报告（原版保留）
    # ============================================================

    def send_top_pump_dump_statistics_report(
        self,
        assets,
        interval,
        top_pump_enabled=True,
        top_dump_enabled=True,
        additional_stats_enabled=True,
        no_of_reported_coins=5,
    ):

        if not top_pump_enabled or not top_dump_enabled:
            return

        message = "*[{0} Interval]*\n\n".format(interval)

        if top_pump_enabled:
            pump_sorted_list = sorted(
                assets,
                key=lambda item: item[interval]["change_current"],
                reverse=True,
            )[0:no_of_reported_coins]

            message += "{0} *Top {1} Pumps*\n".format(
                self.pump_emoji, no_of_reported_coins
            )

            for asset in pump_sorted_list:
                message += "- {0}: _{1:.2f}_%\n".format(
                    asset["symbol"], asset[interval]["change_current"] * 100
                )
            message += "\n"

        if top_dump_enabled:
            dump_sorted_list = sorted(
                assets, key=lambda item: item[interval]["change_current"]
            )[0:no_of_reported_coins]

            message += "{0} *Top {1} Dumps*\n".format(
                self.dump_emoji, no_of_reported_coins
            )

            for asset in dump_sorted_list:
                message += "- {0}: _{1:.2f}_%\n".format(
                    asset["symbol"], asset[interval]["change_current"] * 100
                )

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
            avg_change * 100,
            self.pump_emoji,
            up,
            self.dump_emoji,
            down,
        )