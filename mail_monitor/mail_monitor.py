#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮件监控脚本 - 每小时检查QQ邮箱，发现标题含"has sold"的邮件时通过Server酱推送到微信。

使用方法:
  1. 编辑 config.json 填入QQ邮箱、授权码和Server酱SendKey
  2. pip install requests
  3. python mail_monitor.py

配置说明 (config.json):
  - qq_mail.email:        QQ邮箱地址
  - qq_mail.auth_code:    QQ邮箱IMAP授权码
  - serverchan.sendkeys:  Server酱SendKey列表，支持多个，每条消息会推送到所有Key
  - keyword:              邮件标题匹配关键词，默认 "has sold"
  - check_since_hours:    检查最近几小时内的邮件，默认 2

获取QQ邮箱授权码: QQ邮箱 -> 设置 -> 账户 -> POP3/IMAP服务 -> 开启 -> 生成授权码
获取Server酱SendKey: 访问 https://sct.ftqq.com 微信扫码登录 -> 复制SendKey
"""

import imaplib
import email
import email.header
import email.utils
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def apply_environment_config(config):
    """允许GitHub Actions通过Secrets注入敏感配置，不把密码写进仓库。"""
    email_addr = os.getenv("QQ_EMAIL")
    auth_code = os.getenv("QQ_AUTH_CODE")
    sendkeys = os.getenv("SERVERCHAN_SENDKEYS")
    check_since_hours = os.getenv("CHECK_SINCE_HOURS")

    if email_addr:
        config.setdefault("qq_mail", {})["email"] = email_addr
    if auth_code:
        config.setdefault("qq_mail", {})["auth_code"] = auth_code
    if sendkeys:
        config.setdefault("serverchan", {})["sendkeys"] = [
            key.strip() for key in sendkeys.replace(";", ",").split(",") if key.strip()
        ]
    if check_since_hours:
        try:
            config["check_since_hours"] = float(check_since_hours)
        except ValueError:
            raise ValueError("CHECK_SINCE_HOURS 必须是大于0的数字")
    return config


CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
NOTIFIED_PATH = os.path.join(SCRIPT_DIR, "notified_ids.txt")

# QQ邮箱 IMAP 设置
IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993

# Server酱 API
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_notified_ids():
    if not os.path.exists(NOTIFIED_PATH):
        return set()
    with open(NOTIFIED_PATH, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_notified_id(msg_id):
    with open(NOTIFIED_PATH, "a", encoding="utf-8") as f:
        f.write(msg_id + "\n")


def decode_header_value(raw):
    """解码邮件头 (Subject, From 等)"""
    if raw is None:
        return ""
    parts = email.header.decode_header(raw)
    result = []
    for data, charset in parts:
        if isinstance(data, bytes):
            encoding = (charset or "utf-8").lower().replace("_", "-")
            try:
                result.append(data.decode(encoding, errors="replace"))
            except (LookupError, UnicodeError):
                result.append(data.decode("utf-8", errors="replace"))
        else:
            result.append(data)
    return "".join(result)


def get_email_body(msg, max_chars=500):
    """提取邮件正文纯文本，截取前 max_chars 字符"""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
        except Exception:
            pass
    # 去掉多余空白
    body = body.strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "..."
    return body


def send_serverchan(sendkeys, title, desp):
    """通过 Server酱 推送到微信 (支持多个SendKey，全部推送)"""
    for i, key in enumerate(sendkeys):
        url = SERVERCHAN_API.format(key=key)
        try:
            resp = requests.post(url, data={"title": title, "desp": desp}, timeout=15)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                print(f"  [推送成功] SendKey #{i+1}: 消息已发送到微信")
            else:
                print(f"  [推送失败] SendKey #{i+1}: HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"  [推送异常] SendKey #{i+1}: {e}")
        # 多Key之间间隔，避免请求太快
        if i < len(sendkeys) - 1:
            time.sleep(0.5)


def check_mail(config):
    email_addr = config["qq_mail"]["email"]
    auth_code = config["qq_mail"]["auth_code"]
    # 兼容单key和多key配置
    serverchan = config.get("serverchan", {})
    if "sendkeys" in serverchan:
        sendkeys = serverchan["sendkeys"]
    elif "sendkey" in serverchan:
        sendkeys = [serverchan["sendkey"]]
    else:
        sendkeys = []
    if not sendkeys:
        print("[错误] 未配置任何Server酱SendKey，请在配置文件中设置 serverchan.sendkeys")
        return

    keyword = config.get("keyword", "has sold")
    try:
        since_hours = float(config.get("check_since_hours", 2))
    except (TypeError, ValueError):
        raise ValueError("check_since_hours 必须是大于0的数字")
    if since_hours <= 0:
        raise ValueError("check_since_hours 必须是大于0的数字")

    notified_ids = load_notified_ids()

    # 计算精确时间范围。now使用UTC，邮件Date头也会统一转换成UTC比较。
    now_utc = datetime.now(timezone.utc)
    since_dt = now_utc - timedelta(hours=since_hours)
    # IMAP只能按日期粗筛：从起始日期开始，到明天日期之前。
    # 这样凌晨运行时会自动覆盖前一天和当天，超过24小时也能覆盖多天。
    since_str = since_dt.strftime("%d-%b-%Y")
    before_str = (now_utc + timedelta(days=1)).strftime("%d-%b-%Y")

    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"开始检查邮件 (最近 {since_hours:g} 小时，"
        f"{since_dt.strftime('%Y-%m-%d %H:%M')} 至 {now_utc.strftime('%Y-%m-%d %H:%M')} UTC)..."
    )

    # 连接 IMAP
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(email_addr, auth_code)
        mail.select("INBOX")

        # SINCE/BEFORE只能按日期过滤。随后按邮件Date精确过滤小时。
        # BEFORE使用明天日期，避免把未来日期的异常邮件纳入候选。
        status, data = mail.search(None, "SINCE", since_str, "BEFORE", before_str)
        if status != "OK":
            print("[错误] 搜索邮件失败")
            return

        msg_ids = data[0].split()
        print(
            f"[日期粗筛] {since_str} 至 {before_str}（不含），"
            f"共 {len(msg_ids)} 封；开始按时间精筛"
        )

        found = 0
        in_time_range = 0
        skipped_by_time = 0
        skipped_without_date = 0
        for mid in msg_ids:
            # 先只取邮件头，不下载正文和附件
            status, header_data = mail.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT MESSAGE-ID)])")
            if status != "OK":
                continue

            raw_header = next(
                (item for item in header_data if isinstance(item, tuple)), None
            )
            if not raw_header:
                continue
            msg = email.message_from_bytes(raw_header[1])

            subject = decode_header_value(msg.get("Subject"))
            date_str = msg.get("Date", "")

            # 必须成功解析Date，且邮件时间必须在配置的小时范围内。
            mail_time = None
            try:
                dt_tuple = email.utils.parsedate_tz(date_str)
                if dt_tuple:
                    mail_time = datetime.fromtimestamp(
                        email.utils.mktime_tz(dt_tuple), tz=timezone.utc
                    )
            except (TypeError, ValueError, OverflowError):
                pass

            if mail_time is None:
                skipped_without_date += 1
                continue
            if mail_time < since_dt or mail_time > now_utc:
                skipped_by_time += 1
                continue

            in_time_range += 1
            if keyword.lower() not in subject.lower():
                continue

            # 只有时间范围内且标题命中的邮件才下载完整内容。
            status, full_data = mail.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            raw_email = next(
                (item for item in full_data if isinstance(item, tuple)), None
            )
            if not raw_email:
                continue
            msg = email.message_from_bytes(raw_email[1])
            subject = decode_header_value(msg.get("Subject"))
            sender = decode_header_value(msg.get("From"))
            date_str = msg.get("Date", "")

            # 用 message-id 去重
            message_id = msg.get("Message-ID", "").strip() or mid.decode()
            if message_id in notified_ids:
                print(f"  [跳过] 已推送过: {subject}")
                continue

            print(f"  [命中] {subject}")
            body = get_email_body(msg)
            desp = (
                f"**发件人**: {sender}\n\n"
                f"**时间**: {date_str}\n\n"
                f"**主题**: {subject}\n\n"
                f"---\n\n"
                f"{body}"
            )

            send_serverchan(sendkeys, f"域名售出提醒: {subject[:50]}", desp)
            save_notified_id(message_id)
            notified_ids.add(message_id)
            found += 1
            time.sleep(1)

        print(f"[时间范围] 精确范围内共 {in_time_range} 封，开始按标题过滤关键词: {keyword}")
        if skipped_by_time > 0:
            print(f"[时间过滤] 跳过了 {skipped_by_time} 封超出 {since_hours:g} 小时的邮件")
        if skipped_without_date > 0:
            print(f"[时间过滤] 跳过了 {skipped_without_date} 封无法解析时间的邮件")

        if found == 0:
            print("[完成] 没有新的包含 'has sold' 的邮件")
        else:
            print(f"[完成] 共推送了 {found} 条提醒")

    finally:
        mail.logout()


def main():
    config = apply_environment_config(load_config())
    check_mail(config)


if __name__ == "__main__":
    main()
