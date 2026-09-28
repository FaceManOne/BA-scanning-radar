import logging
import requests
import time

from time import sleep
from utils import ConversionUtils


class BinancePumpAndDumpAlerter:
    def __init__(
        self,
        api_url,
        watchlist,
        blacklist,
        pairs_of_interest,
        chart_intervals,
        outlier_intervals,
        top_report_intervals,
        extract_interval,
        retry_interval,
        reset_interval,
        top_pump_enabled,
        top_dump_enabled,
        additional_statistics_enabled,
        no_of_reported_coins,
        dump_enabled,
        check_new_listing_enabled,
        top_report_nearest_hour,
        telegram,
        report_generator,
        summary_config,
        hourly_report_config,
        stock_symbols,
    ):
        self.api_url = api_url
        self.watchlist = watchlist
        self.blacklist = blacklist
        self.pairs_of_interest = pairs_of_interest
        self.outlier_intervals = outlier_intervals
        self.extract_interval = extract_interval
        self.retry_interval = retry_interval
        self.reset_interval = reset_interval
        self.top_pump_enabled = top_pump_enabled
        self.top_dump_enabled = top_dump_enabled
        self.additional_statistics_enabled = additional_statistics_enabled
        self.no_of_reported_coins = no_of_reported_coins
        self.dump_enabled = dump_enabled
        self.check_new_listing_enabled = check_new_listing_enabled
        self.telegram = telegram
        self.report_generator = report_generator

        self.summary_config = summary_config
        self.hourly_report_config = hourly_report_config
        self.last_summary_time = int(time.time())
        self.last_hourly_report_time = int(time.time())
        self.stock_symbols = stock_symbols

        self.logger = logging.getLogger("pump-and-dump-alerter")

        self.initial_time = int(time.time())
        nearest_hour = self.initial_time - (self.initial_time % 3600) + 3600
        self.logger.info(
            "Nearest hour is %i seconds away", nearest_hour - self.initial_time
        )

        self.chart_intervals = {}
        for interval in chart_intervals:
            self.chart_intervals[interval] = {}
            self.chart_intervals[interval][
                "value"
            ] = ConversionUtils.duration_to_seconds(interval)

        self.top_report_intervals = {}
        for interval in top_report_intervals:
            self.top_report_intervals[interval] = {}

            if top_report_nearest_hour:
                self.top_report_intervals[interval]["start"] = nearest_hour
            else:
                self.top_report_intervals[interval]["start"] = self.initial_time

            self.top_report_intervals[interval][
                "value"
            ] = ConversionUtils.duration_to_seconds(interval)

    @staticmethod
    def extract_ticker_data(symbol, assets):
        for asset in assets:
            if asset["symbol"] == symbol:
                return asset

    @staticmethod
    def create_new_asset(symbol, chart_intervals):
        asset = {"symbol": symbol, "price": [], "volume": []}

        asset["push_level_up"] = 0
        asset["push_level_down"] = 0

        for interval in chart_intervals:
            asset[interval] = {}
            asset[interval]["change_current"] = 0
            asset[interval]["change_last"] = 0
            asset[interval]["change_volume"] = 0

        return asset

    def retrieve_exchange_assets(self, api_url):
        try:
            self.logger.debug(
                "Retrieving price information from the ticker. ApiUrl: %s.", api_url
            )
            return requests.get(api_url).json()
        except Exception as e:
            self.logger.error(
                "Issue occurred while getting prices. Error: %s.",
                e,
                exc_info=True,
            )
            sleep(5)
            return self.retrieve_exchange_assets(api_url)

    def fetch_stock_symbols(self):
        """从币安合约 exchangeInfo 拉取所有非加密货币的交易对（TradFi）。"""
        try:
            url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
            resp = requests.get(url, timeout=10).json()
            symbols = set()
            for s in resp.get("symbols", []):
                ut = s.get("underlyingType")
                if ut is not None and ut != "COIN":
                    symbols.add(s["symbol"])
            self.logger.info("【分类】拉取到 %d 个 TradFi 合约。", len(symbols))
            return symbols
        except Exception as e:
            self.logger.error("【分类】拉取 TradFi 合约列表失败：%s", e)
            return set()

    def is_symbol_valid(self, symbol, watchlist, blacklist, pairs_of_interest):
        if len(watchlist) > 0:
            if symbol not in watchlist:
                self.logger.debug("Ignoring symbol not in watchlist: %s.", symbol)
                return False
            return True

        if len(blacklist) > 0:
            if symbol in blacklist:
                self.logger.info(
                    "Ignoring symbol found in blacklist: %s.", symbol)
                return False

        is_in_pairs_of_interest = False
        for pair in pairs_of_interest:
            if symbol.endswith(pair):
                is_in_pairs_of_interest = True
                break

        if not is_in_pairs_of_interest:
            self.logger.debug("Ignoring symbol not in pairsOfInterests: %s.", symbol)
            return False

        for pair in pairs_of_interest:
            coin = symbol.replace(pair, "")
            if (
                coin.endswith("UP")
                or coin.endswith("DOWN")
                or coin.endswith("BULL")
                or coin.endswith("BEAR")
            ):
                self.logger.debug("Ignoring leverage symbol: %s.", symbol)
                return False

        return True

    def filter_and_convert_assets(self, exchange_assets, watchlist, blacklist, pairs_of_interest, chart_intervals):
        filtered_assets = []

        if not isinstance(exchange_assets, list):
            self.logger.error("拉取到的资产数据不是列表，本次跳过。")
            return filtered_assets

        for exchange_asset in exchange_assets:
            if isinstance(exchange_asset, str):
                import json
                try:
                    exchange_asset = json.loads(exchange_asset)
                except Exception as e:
                    self.logger.error(f"解析资产数据失败: {e}")
                    continue

            if not isinstance(exchange_asset, dict):
                self.logger.error("资产数据格式异常，跳过。")
                continue

            symbol = exchange_asset["symbol"]

            if self.is_symbol_valid(symbol, watchlist, blacklist, pairs_of_interest):
                filtered_assets.append(self.create_new_asset(symbol, chart_intervals))
                self.logger.info("Adding symbol: %s.", symbol)

        self.logger.info("【调试】共检查了 %d 个 symbol，过滤后剩下 %d 个。", len(exchange_assets), len(filtered_assets))
        return filtered_assets

    def update_all_monitored_assets_and_send_news_messages(
        self,
        monitored_assets,
        exchange_assets,
        current_time,
        dump_enabled,
        chart_intervals,
        extract_interval,
        outlier_intervals,
    ):
        for asset in monitored_assets:
            exchange_asset = self.extract_ticker_data(asset["symbol"], exchange_assets)
            asset["price"].append(float(exchange_asset["price"]))

            self.calculate_asset_change(
                asset,
                chart_intervals,
                extract_interval,
            )

            self.report_generator.send_pump_dump_message(
                asset,
                chart_intervals,
                outlier_intervals,
                current_time,
                dump_enabled,
            )

    def calculate_asset_change(
        self,
        asset,
        chart_intervals,
        extract_interval,
    ):
        asset_length = len(asset["price"])

        for interval in chart_intervals:
            data_points = chart_intervals[interval]["value"] // extract_interval

            if data_points >= asset_length:
                self.logger.debug(
                    "Not enough datapoints (%s/%s) for interval: %s",
                    asset_length,
                    data_points,
                    interval,
                )
                break

            current_price = asset["price"][-1]
            if current_price == 0:
                self.logger.warning(
                    "Received zero price for asset %s, skipping calculation",
                    asset["symbol"]
                )
                change = 0
            else:
                price_delta = current_price - asset["price"][-1 - data_points]
                change = price_delta / current_price

            self.logger.debug(
                "Calculated asset: %s for interval: %s with change: %s",
                asset["symbol"],
                interval,
                change,
            )

            asset[interval]["change_last"] = asset[interval]["change_current"]
            asset[interval]["change_current"] = change

        return asset

    def reset_prices_data_when_due(
        self,
        initial_time,
        current_time,
        reset_interval,
        extract_interval,
        assets,
        chart_intervals,
    ):
        if current_time - initial_time > reset_interval:

            message = "Emptying price data to prevent memory errors."
            self.logger.debug(message)
            self.telegram.send_generic_message(message, is_alert_chat=True)

            lastInterval = "1s"
            for interval in chart_intervals:
                lastInterval = interval

            data_points = chart_intervals[lastInterval]["value"] // extract_interval
            one_hour_points = 3600 // extract_interval
            data_points = max(data_points, one_hour_points)

            for asset in assets:
                asset["price"] = asset["price"][-1 - data_points :]

            initial_time = current_time

        return initial_time

    def add_new_asset_listings(
        self,
        initial_assets,
        filtered_assets,
        exchange_assets,
        watchlist,
        blacklist,
        pairs_of_interest,
        chart_intervals,
    ):
        if not isinstance(initial_assets, list):
            self.logger.error("initial_assets 不是列表，跳过新币检查。")
            return filtered_assets

        if len(initial_assets) >= len(exchange_assets):
            self.logger.debug("No new listing found.")
            return filtered_assets

        init_symbols = [asset["symbol"] for asset in initial_assets]
        retrieved_symbols_to_add = [
            exchange_asset["symbol"]
            for exchange_asset in exchange_assets
            if exchange_asset["symbol"] not in init_symbols
        ]

        self.logger.debug("New listings found: %s.", retrieved_symbols_to_add)

        filtered_symbols_to_add = []
        for symbol in retrieved_symbols_to_add:
            if self.is_symbol_valid(symbol, watchlist, blacklist, pairs_of_interest):
                filtered_symbols_to_add.append(symbol)
                filtered_assets.append(self.create_new_asset(symbol, chart_intervals))

        self.logger.debug("Filtered new listings found: %s.", filtered_symbols_to_add)

        if len(filtered_symbols_to_add) > 0:
            self.report_generator.send_new_listings(filtered_symbols_to_add)

        return filtered_assets

    def check_and_send_top_pump_dump_statistics_report(
        self,
        assets,
        current_time,
        top_report_intervals,
        top_pump_enabled,
        top_dump_enabled,
        additional_stats_enabled,
        no_of_reported_coins,
    ):
        for interval in top_report_intervals:
            if (
                current_time
                > top_report_intervals[interval]["start"]
                + top_report_intervals[interval]["value"]
                + 1
            ):
                top_report_intervals[interval]["start"] = current_time - (current_time % ConversionUtils.duration_to_seconds(interval))

                self.logger.debug(
                    "Sending out top pump dump report. Interval: %s.", interval
                )

                self.report_generator.send_top_pump_dump_statistics_report(
                    assets,
                    interval,
                    top_pump_enabled,
                    top_dump_enabled,
                    additional_stats_enabled,
                    no_of_reported_coins,
                )

    def run(self):
        self.stock_symbols = self.fetch_stock_symbols()

        initial_assets = self.retrieve_exchange_assets(self.api_url)

        filtered_assets = self.filter_and_convert_assets(
            initial_assets,
            self.watchlist,
            self.blacklist,
            self.pairs_of_interest,
            self.chart_intervals,
        )

        message = "*Bot has started.* Following _{0}_ pairs."
        self.telegram.send_generic_message(message, len(filtered_assets))
        if self.telegram.is_alert_chat_enabled():
            self.telegram.send_generic_message(
                message,
                len(filtered_assets),
                is_alert_chat=True,
            )

        while True:
            start_loop_time = time.time()
            loop_time = int(start_loop_time)

            exchange_assets = self.retrieve_exchange_assets(self.api_url)

            if self.check_new_listing_enabled:
                filtered_assets = self.add_new_asset_listings(
                    initial_assets,
                    filtered_assets,
                    exchange_assets,
                    self.watchlist,
                    self.blacklist,
                    self.pairs_of_interest,
                    self.chart_intervals,
                )
                initial_assets = exchange_assets

            self.update_all_monitored_assets_and_send_news_messages(
                filtered_assets,
                exchange_assets,
                loop_time,
                self.dump_enabled,
                self.chart_intervals,
                self.extract_interval,
                self.outlier_intervals,
            )

            self.check_and_send_top_pump_dump_statistics_report(
                filtered_assets,
                loop_time,
                self.top_report_intervals,
                self.top_pump_enabled,
                self.top_dump_enabled,
                self.additional_statistics_enabled,
                self.no_of_reported_coins,
            )

            self.initial_time = self.reset_prices_data_when_due(
                self.initial_time,
                loop_time,
                self.reset_interval,
                self.extract_interval,
                filtered_assets,
                self.chart_intervals,
            )

            # ===== 每15分钟（对齐整点）发汇总 + 重置档位 =====
            if self.summary_config.get("enabled", False):
                now = loop_time
                minute = (now // 60) % 60
                if minute in (0, 15, 30, 45):
                    if now - self.last_summary_time >= 14 * 60:
                        self.report_generator.send_summary_report(
                            filtered_assets,
                            self.chart_intervals,
                            self.summary_config,
                        )
                        for asset in filtered_assets:
                            asset["push_level_up"] = 0
                            asset["push_level_down"] = 0
                        self.last_summary_time = now

            # ===== 每小时整点，1小时榜单 =====
            if self.hourly_report_config.get("enabled", False):
                now = loop_time
                minute = (now // 60) % 60
                if minute == 0:
                    if now - self.last_hourly_report_time >= 55 * 60:
                        self.report_generator.send_hourly_report(
                            filtered_assets,
                            self.chart_intervals,
                            self.extract_interval,
                            self.hourly_report_config,
                        )
                        self.last_hourly_report_time = now

            end_loop_time = time.time()

            self.logger.info(
                "Extracting loop started at %d and finished at %d. Taking %f seconds.",
                start_loop_time,
                end_loop_time,
                end_loop_time - start_loop_time,
            )

            if end_loop_time < start_loop_time + self.extract_interval:
                sleep_time = start_loop_time + self.extract_interval - end_loop_time
                self.logger.debug("Now sleeping %f seconds.", sleep_time)
                sleep(sleep_time)