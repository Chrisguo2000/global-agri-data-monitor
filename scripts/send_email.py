#!/usr/bin/env python3
import argparse
import os
import smtplib
import ssl
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
    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls(context=context)
            server.login(smtp_username, smtp_password)
            server.send_message(message)

    print(f"Email sent to {mail_to}")


if __name__ == "__main__":
    main()
