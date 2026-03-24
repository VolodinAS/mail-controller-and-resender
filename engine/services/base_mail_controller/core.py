import imaplib
import os
import smtplib
import subprocess
import traceback
from abc import ABC
from datetime import date, datetime
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Iterator, Optional

from onco_cola_utils import log, logerr, loginf, logwarn


class BaseMailController(ABC):
    def __init__(self):
        self._debug = False
    
    def connect_imap(
        self, host: str, port: int, username: str, password: str
    ) -> imaplib.IMAP4_SSL:
        loginf(f"Connecting to IMAP {host}:{port} as {username}")
        try:
            mail = imaplib.IMAP4_SSL(host, port)
            mail.login(username, password)
            loginf("IMAP login successful")
            return mail
        except Exception as e:
            logerr(f"IMAP connection/login failed: {e}")
            logerr(traceback.format_exc())
            raise
    
    def connect_smtp(
        self, host: str, port: int, username: str, password: str
    ) -> smtplib.SMTP_SSL | smtplib.SMTP:
        loginf(f"Connecting to SMTP {host}:{port}")
        try:
            if port == 465:
                server = smtplib.SMTP_SSL(host, port)
            else:
                server = smtplib.SMTP(host, port)
                server.starttls()
            server.login(username, password)
            loginf("SMTP login successful")
            return server
        except Exception as e:
            logerr(f"SMTP connection/login failed: {e}")
            logerr(traceback.format_exc())
            raise
    
    def fetch_unread_messages(
        self, mail: imaplib.IMAP4_SSL, sender_filter: Optional[str] = None
    ) -> Iterator[tuple[str, bytes]]:
        loginf("Selecting INBOX mailbox...")
        try:
            status, select_resp = mail.select("INBOX")
            if status != "OK":
                logerr(f"Failed to select INBOX: {select_resp}")
                return
            loginf(f"INBOX selected. Response: {select_resp}")
        except Exception as e:
            logerr(f"Error selecting INBOX: {e}")
            logerr(traceback.format_exc())
            return
        
        # Search for UNSEEN messages
        loginf("Searching for UNSEEN messages...")
        try:
            status, messages = mail.search(None, "UNSEEN")
            if status != "OK":
                logerr(f"IMAP search failed: {messages}")
                return
            uids = messages[0].split() if messages[0] else []
            loginf(f"Found {len(uids)} UNSEEN message(s): UIDs = {uids}")
        except Exception as e:
            logerr(f"Error during IMAP search: {e}")
            logerr(traceback.format_exc())
            return
        
        if not uids:
            loginf("No unread messages found.")
            return
        
        for uid in uids:
            uid_str = uid.decode()
            loginf(f"Fetching raw email for UID {uid_str}...")
            try:
                typ, data = mail.fetch(uid, "(RFC822)")
                if typ != "OK":
                    logerr(f"Failed to fetch UID {uid_str}: {data}")
                    continue
                raw_email = data[0][1]
                if raw_email is None:
                    logerr(f"Empty email body for UID {uid_str}")
                    continue
                
                # Parse From header to apply sender filter
                if sender_filter:
                    parser = BytesParser(policy=policy.default)
                    msg = parser.parsebytes(raw_email)
                    from_header = msg.get("From", "")
                    from_addr = parseaddr(from_header)[1]
                    loginf(
                        f"UID {uid_str} is from: '{from_header}' → extracted address: {from_addr}"
                    )
                    if from_addr.lower() != sender_filter.lower():
                        loginf(
                            f"Skipping UID {uid_str}: sender {from_addr} does not match expected {sender_filter}"
                        )
                        continue
                
                yield uid_str, raw_email
            
            except Exception as e:
                logerr(f"Error processing UID {uid_str}: {e}")
                logerr(traceback.format_exc())
                continue
    
    def extract_attachments(
        self, raw_email: bytes, target_filename_pattern: Optional[str] = None
    ) -> list[tuple[str, bytes]]:
        """
        Returns list of (filename, content_bytes)
        """
        from email.parser import BytesParser
        from email import policy
        import fnmatch
        
        parser = BytesParser(policy=policy.default)
        msg = parser.parsebytes(raw_email)
        attachments = []
        
        for part in msg.walk():
            if part.is_attachment():
                filename = part.get_filename()
                if not filename:
                    continue
                # Normalize filename (decode if needed)
                decoded_fragments = decode_header(filename)
                decoded_filename = ""
                for fragment, encoding in decoded_fragments:
                    if isinstance(fragment, bytes):
                        decoded_filename += fragment.decode(encoding or "utf-8")
                    else:
                        decoded_filename += fragment
                filename = decoded_filename
                
                if target_filename_pattern:
                    # Case-insensitive match for patterns like *.zip
                    if not fnmatch.fnmatch(filename.lower(), target_filename_pattern.lower()):
                        continue
                attachments.append((filename, part.get_payload(decode=True)))
        return attachments
    
    @staticmethod
    def _extract_email_date(email_date: datetime | str | None) -> date:
        """
        Извлекает дату из письма в формате date.
        Поддерживает: datetime, str (RFC2822, ISO), None.
        """
        from datetime import date, datetime
        from email.utils import parsedate_to_datetime
        import re
        
        if email_date is None:
            logwarn("_extract_email_date: email_date is None, using today")
            return date.today()
        
        if isinstance(email_date, datetime):
            return email_date.date()
        
        if isinstance(email_date, date):
            return email_date
        
        if isinstance(email_date, str):
            loginf(f"_extract_email_date: parsing string '{email_date}'")
            
            # 1. Пробуем email.utils.parsedate_to_datetime (RFC2822)
            try:
                parsed_dt = parsedate_to_datetime(email_date)
                loginf(
                    f"_extract_email_date: parsed via parsedate_to_datetime -> {parsed_dt.date()}"
                )
                return parsed_dt.date()
            except (ValueError, TypeError, IndexError) as e:
                logwarn(f"_extract_email_date: parsedate_to_datetime failed: {e}")
            
            # 2. Пробуем извлечь дату через регекс (формат: 2026-03-22)
            match = re.search(r'(\d{4}-\d{2}-\d{2})', email_date)
            if match:
                try:
                    extracted = date.fromisoformat(match.group(1))
                    loginf(f"_extract_email_date: extracted via regex -> {extracted}")
                    return extracted
                except ValueError as e:
                    logwarn(f"_extract_email_date: fromisoformat failed: {e}")
            
            # 3. Пробуем dateutil.parser (если установлен)
            try:
                from dateutil import parser as date_parser
                parsed_dt = date_parser.parse(email_date)
                loginf(f"_extract_email_date: parsed via dateutil -> {parsed_dt.date()}")
                return parsed_dt.date()
            except ImportError:
                logwarn("_extract_email_date: dateutil not installed, skipping")
            except (ValueError, TypeError) as e:
                logwarn(f"_extract_email_date: dateutil.parser failed: {e}")
            
            # Fallback
            logerr(f"_extract_email_date: all parsers failed for '{email_date}', using today")
            return date.today()
        
        logerr(f"_extract_email_date: unknown type {type(email_date)}, using today")
        return date.today()
    
    def send_email_with_attachment(
        self,
        smtp_conn: smtplib.SMTP | smtplib.SMTP_SSL,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]],
        same_subject: bool = True,
        original_subject: str | None = None,
        same_body: bool = True,
        original_body: str | None = None,
        email_date: date | str | None = None,
    ) -> None:
        from email.message import EmailMessage
        from pathlib import Path
        import os
        
        # ← ОТЛАДКА: логируем входные параметры
        loginf(
            f"[DEBUG] send_email_with_attachment: email_date={email_date!r}, type={type(email_date)}"
        )
        
        final_subject = original_subject if same_subject and original_subject else subject
        final_body = original_body if same_body and original_body is not None else body
        
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = final_subject
        msg.set_content(final_body)
        
        base_dir_str = os.getenv('ATTACHMENT_STORAGE_DIR', 'files')
        base_path = Path(base_dir_str)
        if not base_path.is_absolute():
            base_path = Path.cwd() / base_path
        
        # ← ОТЛАДКА: извлекаем дату с логированием
        folder_date = self._extract_email_date(email_date)
        date_folder = folder_date.strftime("%Y-%m-%d")
        loginf(f"[DEBUG] folder_date={folder_date}, date_folder={date_folder}")
        
        target_dir = base_path / date_folder
        target_dir.mkdir(parents=True, exist_ok=True)
        
        for filename, content in attachments:
            file_path = target_dir / filename
            if file_path.exists():
                from datetime import datetime
                timestamp = datetime.now().strftime("%H%M%S")
                file_path = target_dir / f"{timestamp}_{filename}"
            file_path.write_bytes(content)
            log(f"File saved to: {file_path.resolve()}")
            
            if filename.lower().endswith(".zip"):
                maintype, subtype = "application", "zip"
            else:
                maintype, subtype = "application", "octet-stream"
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
        
        smtp_conn.send_message(msg)
        loginf(f"Email sent to {recipient} with subject: '{final_subject}' (folder: {date_folder})")
    
    def send_telegram_alert(self, message: str) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_ids_str = os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "")
        
        if not token or not chat_ids_str.strip():
            return  # Telegram не настроен — просто молча выходим
        
        chat_ids = [cid.strip() for cid in chat_ids_str.split(",") if cid.strip()]
        
        for chat_id in chat_ids:
            try:
                subprocess.run(
                    [
                        "curl", "-s", "-X", "POST",
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        "-d", f"chat_id={chat_id}",
                        "-d", f"text={message}",
                        "-d", "parse_mode=HTML"
                    ],
                    check=True,
                    capture_output=True,
                )
            except Exception as e:
                logerr(f"Failed to send Telegram alert to {chat_id}: {e}")
