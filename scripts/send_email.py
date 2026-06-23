#!/usr/bin/env python3
import argparse
import os
import smtplib
import ssl
import socket
from email.message import EmailMessage
from pathlib import Path


def env_required(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def attach_file(message, path):
    file_path = Path(path)
    data = file_path.read_bytes()
    suffix = file_path.suffix.lower()
    if suffix == ".xlsx":
        maintype = "application"
        subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix == ".csv":
        maintype = "text"
        subtype = "csv"
    elif suffix == ".zip":
        maintype = "application"
        subtype = "zip"
    else:
        maintype = "application"
        subtype = "octet-stream"
    message.add_attachment(data, maintype=maintype, subtype=subtype, filename=file_path.name)


def fail_with_hint(hint, exc):
    raise SystemExit(f"{hint}\n原始错误: {exc.__class__.__name__}: {exc}") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--attachments", nargs="+", required=True)
    args = parser.parse_args()

    smtp_host = env_required("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT") or "587")
    smtp_username = env_required("SMTP_USERNAME")
    smtp_password = env_required("SMTP_PASSWORD")
    mail_from = os.environ.get("MAIL_FROM") or smtp_username
    mail_to = env_required("MAIL_TO")

    message = EmailMessage()
    message["Subject"] = args.subject
    message["From"] = mail_from
    message["To"] = mail_to
    message.set_content(args.body)

    for path in args.attachments:
        attach_file(message, path)

    context = ssl.create_default_context()
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
                server.login(smtp_username, smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls(context=context)
                server.login(smtp_username, smtp_password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        fail_with_hint(
            "SMTP 登录失败: 请确认 SMTP_USERNAME 是发件邮箱, "
            "SMTP_PASSWORD 是邮箱的 SMTP 授权码/应用专用密码, 不是网页登录密码。",
            exc,
        )
    except smtplib.SMTPRecipientsRefused as exc:
        fail_with_hint("收件邮箱被 SMTP 服务器拒绝: 请检查 MAIL_TO 是否正确。", exc)
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, socket.gaierror, OSError) as exc:
        fail_with_hint(
            "SMTP 连接失败: 请检查 SMTP_HOST/SMTP_PORT 是否匹配发件邮箱, "
            "并确认该邮箱已开启 SMTP 服务。",
            exc,
        )
    except ssl.SSLError as exc:
        fail_with_hint("SMTP SSL/TLS 握手失败: 请检查 SMTP_PORT, 587 通常使用 STARTTLS, 465 使用 SSL。", exc)

    print(f"Email sent to {mail_to}")


if __name__ == "__main__":
    main()
