import smtplib
import logging

from email.mime.text import MIMEText
from email.header import Header


class EmailSender:
    def __init__(self, smtp_server, smtp_port, sender_email, auth_code, receiver_email):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.auth_code = auth_code
        self.receiver_email = receiver_email
        self.logger = logging.getLogger("email-sender")

    def send(self, subject, body):
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = self.sender_email
            msg["To"] = self.receiver_email

            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=10) as server:
                server.login(self.sender_email, self.auth_code)
                server.sendmail(self.sender_email, [self.receiver_email], msg.as_string())
            self.logger.info("邮件已发送：%s", subject)
        except Exception as e:
            self.logger.error("邮件发送失败：%s", e)