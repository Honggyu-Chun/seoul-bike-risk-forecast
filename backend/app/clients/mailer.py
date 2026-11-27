"""메일 발송(SMTP). 이메일 인증과 비밀번호 재설정 링크를 보낸다.

일반 SMTP라 Gmail(앱 비밀번호), Resend·SES의 SMTP, 개발용 Mailpit 어느 것이든 설정 값만 바꿔 쓴다.
smtplib는 동기 라이브러리라 스레드풀에서 돌린다(발송하는 동안 다른 API가 멈추지 않게).

로그에는 실패 종류만 남긴다. 받는 주소, 본문(링크 속 토큰), SMTP 비밀번호는 남기지 않는다.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import MailUnavailable

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None:
        """보내지 못하면 MailUnavailable"""


class SmtpMailer:
    def __init__(self, settings: Settings):
        self._settings = settings

    def _send_sync(self, message: EmailMessage) -> None:
        s = self._settings
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(message)

    async def send(self, to: str, subject: str, body: str) -> None:
        if not self._settings.smtp_host:
            logger.error("mail not sent: SMTP_HOST is not set")
            raise MailUnavailable()
        message = EmailMessage()
        message["From"] = self._settings.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            await run_in_threadpool(self._send_sync, message)
        except (OSError, smtplib.SMTPException) as exc:  # 접속 실패·시간 초과·인증 실패·수신 거부
            logger.error("mail not sent: %s", type(exc).__name__)
            raise MailUnavailable() from None
