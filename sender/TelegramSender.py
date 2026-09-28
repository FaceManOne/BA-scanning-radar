import logging
import time

from concurrent.futures import ThreadPoolExecutor
from telegram import Bot, ParseMode
from telegram.error import RetryAfter
from telegram.utils.request import Request
from time import sleep


class TelegramSender:
    def __init__(
        self,
        token,
        chat_id,
        alert_chat_id=0,
        bot_emoji="\U0001F916",  # 🤖
        top_emoji="\U0001F3C6",  # 🏆
        news_emoji="\U0001F4F0",  # 📰
    ):

        self.token = token
        self.chat_id = chat_id
        self.alert_chat_id = alert_chat_id

        self.bot_emoji = bot_emoji
        self.top_emoji = top_emoji
        self.news_emoji = news_emoji

        self.telegram_executor = ThreadPoolExecutor(max_workers=3)

        self.request = Request(con_pool_size=3, proxy_url='http://127.0.0.1:10808')
        self.bot = Bot(self.token, request=self.request)

        self.logger = logging.getLogger("telegram-sender")

        # ===== 新增：每小时最多推送20条警报的计数器 =====
        self._push_count = 0
        self._push_reset_time = int(time.time())

    def is_alert_chat_enabled(self):
        return self.alert_chat_id != 0 and self.alert_chat_id != self.chat_id

    def send_message(self, message, is_alert_chat=False):

        # ===== 新增：每小时最多推送20条警报的限制 =====
        current_time = int(time.time())
        if current_time - self._push_reset_time >= 3600:
            self._push_count = 0
            self._push_reset_time = current_time

        if is_alert_chat and self._push_count >= 20:
            self.logger.warning("已达到每小时20条警报限制，跳过本条推送。")
            return
        # ================================================

        chat_id = self.chat_id if not is_alert_chat else self.alert_chat_id

        def push_message(bot, chat_id, message):
            self.logger.info(message)

            try:
                bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
            except RetryAfter as e:
                self.logger.error(
                    "Flood limit is exceeded. Sleep {} seconds.", e.retry_after
                )
                sleep(e.retry_after)
                # Resend message to the queue
                self.send_message(message, is_alert_chat)
            except Exception as e:
                self.logger.error(str(e))

        self.telegram_executor.submit(
            lambda p: push_message(*p), (self.bot, chat_id, message)
        )

        # ===== 新增：警报发出后计数 +1 =====
        if is_alert_chat:
            self._push_count += 1
        # ==================================

    def send_generic_message(self, message, args=None, is_alert_chat=False):
        if args is not None:
            message = message.format(args)
        self.send_message(self.bot_emoji + " " + message, is_alert_chat)

    def send_report_message(self, message, args=None, is_alert_chat=False):
        if args is not None:
            message = message.format(args)
        self.send_message(self.top_emoji + " " + message, is_alert_chat)

    def send_news_message(self, message, args=None, is_alert_chat=False):
        if args is not None:
            message = message.format(args)
        self.send_message(self.news_emoji + " " + message, is_alert_chat)