import imaplib
import smtplib
import traceback
from abc import ABC
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Iterator, Optional

from onco_cola_utils import logerr, loginf


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
    ) -> None:
        from email.message import EmailMessage
        
        final_subject = original_subject if same_subject and original_subject else subject
        final_body = original_body if same_body and original_body is not None else body  # ✅
        
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = recipient
        msg["Subject"] = final_subject
        msg.set_content(final_body)  # ✅ Используем final_body, а не body
        
        for filename, content in attachments:
            if filename.lower().endswith(".zip"):
                maintype, subtype = "application", "zip"
            else:
                maintype, subtype = "application", "octet-stream"
            
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )
        
        smtp_conn.send_message(msg)
        loginf(f"Email sent to {recipient} with subject: '{final_subject}'")
