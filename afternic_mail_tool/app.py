#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Afternic 售出邮件监控工具 - 独立版
通过 IMAP 连接 QQ 邮箱，搜索 "has sold at Afternic" 邮件。
完全自包含，不依赖外部项目文件。
电脑/手机均可通过浏览器访问。
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import ssl
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# ── 路径 ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ── IMAP 常量 ──────────────────────────────────────────────
IMAP_HOST = "imap.qq.com"
IMAP_PORT = 993
SEARCH_KEYWORDS = ["has sold at Afternic", "Payment has been sent"]
DEFAULT_EMAIL = "443320390@qq.com"

app = FastAPI(title="Afternic 售出邮件监控")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ════════════════════════════════════════════════════════════
#  配置读写
# ════════════════════════════════════════════════════════════
def read_config() -> dict[str, Any]:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_config(data: dict[str, Any]) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


# ════════════════════════════════════════════════════════════
#  邮件解码
# ════════════════════════════════════════════════════════════
def decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    result: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except (LookupError, Exception):
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def get_email_body(msg: email.message.Message) -> tuple[str, str]:
    text_body = ""
    html_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition", ""))
            if "attachment" in cdisp.lower():
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, Exception):
                decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and not text_body:
                text_body = decoded
            elif ctype == "text/html" and not html_body:
                html_body = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, Exception):
                decoded = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = decoded
            else:
                text_body = decoded
    return text_body, html_body


def strip_html(html_str: str) -> str:
    html_str = re.sub(r"<br\s*/?>", "\n", html_str, flags=re.IGNORECASE)
    html_str = re.sub(r"<[^>]+>", "", html_str)
    html_str = re.sub(r"&nbsp;", " ", html_str)
    html_str = re.sub(r"&amp;", "&", html_str)
    html_str = re.sub(r"&lt;", "<", html_str)
    html_str = re.sub(r"&gt;", ">", html_str)
    html_str = re.sub(r"&quot;", '"', html_str)
    html_str = re.sub(r"&#39;", "'", html_str)
    html_str = re.sub(r"\n{3,}", "\n\n", html_str)
    return html_str.strip()


# ════════════════════════════════════════════════════════════
#  Afternic 邮件解析
# ════════════════════════════════════════════════════════════
def parse_afternic_email(subject: str, text_body: str, html_body: str,
                         date_str: str, from_str: str,
                         match_type: str = "售出") -> dict[str, Any]:
    full_text = text_body if text_body else strip_html(html_body)

    # 域名提取
    domain = ""
    domain_patterns = [
        r"(?:Domain|Domain Name)\s*[:：]\s*([a-zA-Z0-9][a-zA-Z0-9\-]*\.[a-zA-Z]{2,})",
        r"(?:your\s+domain\s+)([a-zA-Z0-9][a-zA-Z0-9\-]*\.[a-zA-Z]{2,})\s+has\s+sold",
        r"([a-zA-Z0-9][a-zA-Z0-9\-]*\.[a-zA-Z]{2,})\s+has\s+sold\s+at\s+Afternic",
        r"\b([a-zA-Z0-9][a-zA-Z0-9\-]{1,}\.[a-zA-Z]{2,})\b",
    ]
    for pat in domain_patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if not m:
            m = re.search(pat, subject, re.IGNORECASE)
        if m:
            domain = m.group(1).lower()
            break

    # 售价提取
    price = ""
    price_patterns = [
        r"(?:Sale\s+Price|Price|Sold\s+Price)\s*[:：]\s*\$?\s*([\d,]+\.?\d*)",
        r"\$\s*([\d,]+\.?\d*)",
        r"(?:sold\s+for|sale\s+price\s+of)\s+\$?\s*([\d,]+\.?\d*)",
    ]
    for pat in price_patterns:
        m = re.search(pat, full_text, re.IGNORECASE)
        if m:
            price = m.group(1)
            break

    # 佣金
    commission = ""
    m = re.search(r"(?:Commission|Fee)\s*[:：]\s*\$?\s*([\d,]+\.?\d*)", full_text, re.IGNORECASE)
    if m:
        commission = m.group(1)

    # 净收入
    payout = ""
    m = re.search(r"(?:Net|Payout|You\s+Receive|Proceeds)\s*[:：]\s*\$?\s*([\d,]+\.?\d*)", full_text, re.IGNORECASE)
    if m:
        payout = m.group(1)

    # 买家
    buyer = ""
    m = re.search(r"(?:Buyer)\s*[:：]\s*(.+?)(?:\n|$)", full_text, re.IGNORECASE)
    if m:
        buyer = m.group(1).strip()

    # 日期
    parsed_date = ""
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt:
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc)
            parsed_date = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        parsed_date = date_str

    return {
        "domain": domain,
        "price": price,
        "commission": commission,
        "payout": payout,
        "buyer": buyer,
        "date": parsed_date,
        "from": decode_header_value(from_str),
        "subject": subject,
        "type": match_type,
        "preview": full_text[:600] if full_text else "",
    }


# ════════════════════════════════════════════════════════════
#  IMAP 搜索
# ════════════════════════════════════════════════════════════
def fetch_afternic_emails(email_addr: str, auth_code: str,
                          search_hours: int = 24,
                          folder: str = "INBOX",
                          search_type: str = "sold") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=search_hours)
    ctx = ssl.create_default_context()
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx)
    try:
        mail.login(email_addr, auth_code)
        mail.select(folder, readonly=True)

        # IMAP SINCE 只能精确到日期；多取一天避免时区边界漏信，随后按邮件时间精确到小时过滤。
        since_date = cutoff_time - timedelta(days=1)
        date_criteria = since_date.strftime("%d-%b-%Y")
        status, data = mail.search(None, "SINCE", date_criteria)

        if status != "OK":
            return results

        ids = data[0].split()
        if not ids:
            return results

        all_ids = ids[-500:]  # 最多 500 封

        # 根据搜索类型选择关键词
        if search_type == "payment":
            kws_lower = [SEARCH_KEYWORDS[1].lower()]  # "payment has been sent"
            match_type = "付款"
        else:
            kws_lower = [SEARCH_KEYWORDS[0].lower()]  # "has sold at afternic"
            match_type = "售出"

        for uid in reversed(all_ids):
            status, msg_data = mail.fetch(uid, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message.from_bytes(raw)

            subject = decode_header_value(msg.get("Subject", ""))
            from_str = msg.get("From", "")
            date_str = msg.get("Date", "")

            try:
                message_time = email.utils.parsedate_to_datetime(date_str)
                if message_time is not None:
                    if message_time.tzinfo is None:
                        message_time = message_time.replace(tzinfo=timezone.utc)
                    else:
                        message_time = message_time.astimezone(timezone.utc)
                    if message_time < cutoff_time:
                        continue
            except (TypeError, ValueError, OverflowError):
                # 极少数邮件缺少有效 Date；为避免漏信，继续按标题和正文判断。
                pass

            subject_lower = subject.lower()
            matched = False
            for kw in kws_lower:
                if kw in subject_lower:
                    matched = True
                    break

            if not matched:
                text_body, html_body = get_email_body(msg)
                combined = (text_body + " " + html_body).lower()
                for kw in kws_lower:
                    if kw in combined:
                        matched = True
                        break
            else:
                text_body, html_body = get_email_body(msg)

            if matched:
                info = parse_afternic_email(subject, text_body, html_body,
                                            date_str, from_str, match_type)
                results.append(info)

    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass

    return results


# ════════════════════════════════════════════════════════════
#  后台任务状态
# ════════════════════════════════════════════════════════════
class TaskState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.status = "就绪"
        self.logs: list[str] = []
        self.results: list[dict[str, Any]] = []

    def reset(self) -> None:
        with self.lock:
            self.running = True
            self.status = "准备中"
            self.logs = []
            self.results = []

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "status": self.status,
                "logs": list(self.logs),
                "count": len(self.results),
                "results": list(self.results),
            }


state = TaskState()


# ════════════════════════════════════════════════════════════
#  API 模型
# ════════════════════════════════════════════════════════════
class ConfigRequest(BaseModel):
    email: str = ""
    auth_code: str = ""
    search_hours: str = "24"
    folder: str = "INBOX"
    save_creds: bool = False


class FetchRequest(BaseModel):
    email: str = ""
    auth_code: str = ""
    search_hours: str = "24"
    folder: str = "INBOX"
    search_type: str = "sold"


# ════════════════════════════════════════════════════════════
#  路由
# ════════════════════════════════════════════════════════════
@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/manifest.json")
def manifest() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"),
                        media_type="application/manifest+json")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    cfg = read_config()
    return {
        "email": cfg.get("email", DEFAULT_EMAIL),
        "search_hours": cfg.get("search_hours", cfg.get("search_days", "24")),
        "folder": cfg.get("folder", "INBOX"),
        "has_auth_code": bool(cfg.get("auth_code", "")),
    }


@app.post("/api/config")
def save_config(req: ConfigRequest) -> dict[str, Any]:
    data: dict[str, Any] = {
        "email": req.email,
        "search_hours": req.search_hours,
        "folder": req.folder,
    }
    if req.save_creds and req.auth_code:
        data["auth_code"] = req.auth_code
    else:
        existing = read_config()
        if existing.get("auth_code"):
            data["auth_code"] = existing["auth_code"]
    write_config(data)
    return {"ok": True}


@app.post("/api/fetch")
def fetch_mails(req: FetchRequest) -> dict[str, Any]:
    with state.lock:
        if state.running:
            raise HTTPException(status_code=409, detail="已有任务正在运行")

    email_addr = req.email.strip()
    auth_code = req.auth_code.strip()
    if not email_addr:
        raise HTTPException(status_code=400, detail="请输入 QQ 邮箱地址")
    if not auth_code:
        raise HTTPException(status_code=400, detail="请输入授权码")

    try:
        hours = int(req.search_hours.strip() or "24")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="搜索小时数必须是整数") from exc
    if hours < 1 or hours > 8760:
        raise HTTPException(status_code=400, detail="搜索小时数必须在 1 到 8760 之间")
    folder = req.folder.strip() or "INBOX"
    search_type = req.search_type.strip() or "sold"
    type_label = "付款" if search_type == "payment" else "售出"

    state.reset()
    state.log(f"[开始] 邮箱: {email_addr}，类型: {type_label}，搜索最近 {hours} 小时，文件夹: {folder}")

    def worker() -> None:
        try:
            state.set_status("连接邮箱中...")
            state.log("[连接] 正在连接 imap.qq.com:993 ...")
            results = fetch_afternic_emails(email_addr, auth_code, hours, folder, search_type)
            with state.lock:
                state.results = results
            state.log(f"[完成] 找到 {len(results)} 封{type_label}邮件")
            state.set_status("完成")
        except Exception as exc:
            state.log(f"[错误] {exc}")
            state.set_status("出错")
        finally:
            with state.lock:
                state.running = False

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    return state.snapshot()


@app.post("/api/stop")
def stop() -> dict[str, Any]:
    state.log("[停止] 已请求停止")
    with state.lock:
        state.running = False
        state.status = "已停止"
    return {"ok": True}


# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
