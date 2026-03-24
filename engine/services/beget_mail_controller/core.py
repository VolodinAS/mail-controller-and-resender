import os
import traceback

from dotenv import load_dotenv
from onco_cola_utils import logerr, loginf, logwarn, value_to_bool

from engine.services.base_mail_controller import BaseMailController


load_dotenv()


# В начале файла

# В __init__ после загрузки других переменных:


class BegetMailController(BaseMailController):
    def __init__(self):
        super().__init__()
        self.imap_host = os.getenv("IMAP_HOST", "imap.beget.com")
        self.imap_port = int(os.getenv("IMAP_PORT", "993"))
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.beget.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.email_user = os.getenv("EMAIL_USER")
        self.email_password = os.getenv("EMAIL_PASSWORD")
        self.sender_email = os.getenv("SENDER_EMAIL", self.email_user)
        self.recipient_email = os.getenv("RECIPIENT_EMAIL")
        self.expected_sender = os.getenv("EXPECTED_SENDER")
        self.attachment_pattern = os.getenv("ATTACHMENT_FILENAME_PATTERN")
        
        self.same_subject = value_to_bool(os.getenv("SAME_SUBJECT", "true"))
        self.same_body = value_to_bool(os.getenv("SAME_BODY", "true"))
        self.set_unread = value_to_bool(os.getenv("SET_UNREAD", "false"))
        
        # Validation
        if not all([self.email_user, self.email_password, self.recipient_email]):
            raise ValueError(
                "Missing required env vars: EMAIL_USER, EMAIL_PASSWORD, RECIPIENT_EMAIL"
            )
    
    def process_incoming_emails(self) -> None:
        loginf("Starting email processing...")
        
        try:
            imap_conn = self.connect_imap(
                self.imap_host, self.imap_port, self.email_user, self.email_password
            )
        except Exception as e:
            error_msg = f"ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n❌ IMAP connection failed: {e}"
            logerr(error_msg)
            self.send_telegram_alert(error_msg)
            return
        
        try:
            uids_and_emails = list(self.fetch_unread_messages(imap_conn, self.expected_sender))
            
            if not uids_and_emails:
                msg = "ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n📭 Нет новых писем для обработки."
                loginf(msg)
                self.send_telegram_alert(msg)
                return
            
            for uid, raw_email in uids_and_emails:
                loginf(f"Processing email UID {uid}")
                
                from email.parser import BytesParser
                from email import policy
                parser = BytesParser(policy=policy.default)
                msg = parser.parsebytes(raw_email)
                
                from email.utils import parsedate_to_datetime
                from datetime import date
                
                email_date_header = msg.get("Date")
                loginf(f"[DEBUG] Email Date header: {email_date_header!r}")
                
                try:
                    if email_date_header:
                        email_datetime = parsedate_to_datetime(email_date_header)
                        email_date_for_folder = email_datetime.date()
                    else:
                        email_date_for_folder = date.today()
                        logwarn("Date header is missing, using today")
                except Exception as e:
                    logwarn(f"Failed to parse Date header: {e}, using today")
                    email_date_for_folder = date.today()
                
                loginf(f"[DEBUG] Parsed email_date_for_folder: {email_date_for_folder}")
                
                original_subject = msg.get("Subject", "[No Subject]")
                loginf(f"Original email subject: '{original_subject}'")
                
                # Извлекаем тело
                original_body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            original_body = part.get_content() or ""
                            break
                        elif part.get_content_type() == "text/html" and not original_body:
                            original_body = part.get_content() or ""
                else:
                    original_body = msg.get_content() or ""
                loginf(f"Extracted body length: {len(original_body)} characters")
                
                attachments = self.extract_attachments(raw_email, self.attachment_pattern)
                if not attachments:
                    warn_msg = (
                        f"ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n📭 Письмо UID {uid} получено, но вложений нет.\n"
                        f"Тема: {original_subject}"
                    )
                    loginf("No attachments found, skipping")
                    self.send_telegram_alert(warn_msg)
                    continue
                
                try:
                    smtp_conn = self.connect_smtp(
                        self.smtp_host, self.smtp_port, self.email_user, self.email_password
                    )
                except Exception as e:
                    error_msg = f"ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n❌ SMTP connection failed for UID {uid}: {e}"
                    logerr(error_msg)
                    self.send_telegram_alert(error_msg)
                    continue
                
                try:
                    self.send_email_with_attachment(
                        smtp_conn=smtp_conn,
                        sender=self.sender_email,
                        recipient=self.recipient_email,
                        subject="[Auto] Forwarded attachment",
                        body="Файл автоматически переслан с сервера Beget.",
                        attachments=attachments,
                        same_subject=self.same_subject,
                        original_subject=original_subject,
                        same_body=self.same_body,
                        original_body=original_body,
                        email_date=email_date_for_folder,
                    )
                    
                    success_msg = (
                        f"ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n✅ Письмо UID {uid} успешно переслано!\n"
                        f"Тема: {original_subject}\n"
                        f"Вложений: {len(attachments)}"
                    )
                    loginf("Email forwarded successfully")
                    self.send_telegram_alert(success_msg)
                    
                    # Управление статусом прочтения
                    if self.set_unread:
                        imap_conn.store(uid, "-FLAGS", "\\Seen")
                        loginf(f"Restored 'unread' status for UID {uid}")
                    else:
                        imap_conn.store(uid, "+FLAGS", "\\Seen")
                        loginf(f"Marked UID {uid} as read")
                
                except Exception as e:
                    fail_msg = (
                        f"ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n❌ Не удалось переслать письмо UID {uid}.\n"
                        f"Тема: {original_subject}\n"
                        f"Ошибка: {e}"
                    )
                    logerr(f"Forwarding failed: {e}")
                    self.send_telegram_alert(fail_msg)
                
                finally:
                    smtp_conn.quit()
        
        except Exception as e:
            critical_error = f"ПЕРЕСЫЛКА ОТ ВЕКТОР:\n\n🔥 Критическая ошибка при обработке писем: {e}"
            logerr(critical_error)
            logerr(traceback.format_exc())
            self.send_telegram_alert(critical_error)
        
        finally:
            try:
                imap_conn.close()
                imap_conn.logout()
            except Exception as e:
                logerr(f"Error closing IMAP connection: {e}")
