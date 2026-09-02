#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聚名 .com 域名 RDAP 增强 + 过滤（命令行 / 可被 web_app 调用）。

数据源：Verisign RDAP 官方接口（免费、无 key、返回 JSON）
  https://rdap.verisign.com/com/v1/domain/{domain}

功能：
  1. 批量查询每个 .com 域名的 注册时间 / 到期时间 / 注册商 / 状态
  2. 过滤：注册时间 >= N 天 且 注册商为聚名（IANA ID 3758 或名称含 juming/聚名）
  3. SQLite 本地缓存（域名 whois 几乎不变，跨天复用，后续只查新增）
  4. 断点续传（已成功缓存的跳过，失败的下次重试）
  5. 输出最终 CSV，新增列：注册时间/注册天数/到期时间/注册商/注册商ID/状态/查询时间

用法：
  python enrich_whois.py 明细.csv
  python enrich_whois.py 明细.csv --workers 20 --min-register-days 60 --registrar juming -o out.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import quote


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RDAP_URL = "https://rdap.verisign.com/com/v1/domain/{domain}"
JUMING_IANA = "3758"          # 聚名（合肥聚名网络科技）IANA 注册商 ID
JUMING_NAME_KEYS = ("juming", "聚名")  # 名称模糊匹配
USER_AGENT = "Mozilla/5.0 (compatible; juming-enrich/1.0)"


# ---------------------------------------------------------------------------
# RDAP 解析
# ---------------------------------------------------------------------------

def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        # 2026-04-06T18:37:19Z
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        # 兜底：取前 10 位 YYYY-MM-DD
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def parse_rdap(d: dict[str, Any]) -> dict[str, Any]:
    """从 RDAP JSON 抽取需要的字段。"""
    reg_date = exp_date = None
    for ev in d.get("events", []) or []:
        action = ev.get("eventAction")
        if action == "registration":
            reg_date = ev.get("eventDate")
        elif action == "expiration":
            exp_date = ev.get("eventDate")

    status = ",".join(d.get("status", []) or [])

    registrar_handle = None
    registrar_name = ""
    for e in d.get("entities", []) or []:
        if "registrar" in (e.get("roles") or []):
            registrar_handle = e.get("handle")
            vc = e.get("vcardArray") or [None, []]
            if isinstance(vc, list) and len(vc) > 1:
                for item in vc[1]:
                    if isinstance(item, list) and item and item[0] == "fn" and len(item) > 3:
                        registrar_name = str(item[3] or "")
                        break
            break

    return {
        "reg_date": reg_date or "",
        "exp_date": exp_date or "",
        "status": status,
        "registrar_handle": str(registrar_handle or ""),
        "registrar_name": registrar_name,
    }


def is_juming(data: dict[str, Any]) -> bool:
    handle = str(data.get("registrar_handle") or "")
    name = str(data.get("registrar_name") or "")
    if handle == JUMING_IANA:
        return True
    name_low = name.lower()
    return any(key in name or key.lower() in name_low for key in JUMING_NAME_KEYS)


def filter_reason(data: dict[str, Any], min_days: int, today: date) -> tuple[bool, str]:
    """返回 (是否通过, 原因)。"""
    if data.get("error"):
        return False, f"查询失败: {data['error'][:40]}"
    if data.get("not_found"):
        return False, "RDAP无记录"
    reg = parse_iso_date(data.get("reg_date"))
    if min_days > 0:
        if reg is None:
            return False, "无注册时间"
        days = (today - reg).days
        if days < min_days:
            return False, f"注册{days}天<{min_days}"
    if not is_juming(data):
        return False, f"注册商非聚名:{data.get('registrar_name','')[:30]}"
    return True, "ok"


# ---------------------------------------------------------------------------
# SQLite 缓存
# ---------------------------------------------------------------------------

def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rdap_cache (
            domain TEXT PRIMARY KEY,
            reg_date TEXT,
            exp_date TEXT,
            status TEXT,
            registrar_handle TEXT,
            registrar_name TEXT,
            json TEXT,
            ok INTEGER,
            fetched_at TEXT
        )"""
    )
    conn.commit()
    return conn


def cache_get(conn: sqlite3.Connection, domain: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT reg_date, exp_date, status, registrar_handle, registrar_name, json, ok, fetched_at "
        "FROM rdap_cache WHERE domain=?",
        (domain,),
    ).fetchone()
    if not row:
        return None
    return {
        "reg_date": row[0] or "",
        "exp_date": row[1] or "",
        "status": row[2] or "",
        "registrar_handle": row[3] or "",
        "registrar_name": row[4] or "",
        "ok": bool(row[6]),
        "fetched_at": row[7] or "",
        "cached": True,
    }


def cache_put(conn: sqlite3.Connection, domain: str, data: dict[str, Any], ok: bool) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO rdap_cache "
        "(domain, reg_date, exp_date, status, registrar_handle, registrar_name, json, ok, fetched_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            domain,
            data.get("reg_date", ""),
            data.get("exp_date", ""),
            data.get("status", ""),
            data.get("registrar_handle", ""),
            data.get("registrar_name", ""),
            json.dumps(data, ensure_ascii=False),
            1 if ok else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 网络查询
# ---------------------------------------------------------------------------

async def fetch_rdap(session: Any, domain: str, retries: int = 3) -> dict[str, Any]:
    url = RDAP_URL.format(domain=quote(domain, safe=""))
    backoff = 1.0
    last_err = ""
    for attempt in range(retries + 1):
        try:
            async with session.get(url) as resp:
                text = await resp.text()
                if resp.status == 404:
                    return {"not_found": True, "reg_date": "", "exp_date": "",
                            "status": "", "registrar_handle": "", "registrar_name": ""}
                if resp.status == 429 or resp.status >= 500:
                    last_err = f"HTTP {resp.status}"
                    raise RuntimeError(last_err)
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}"
                    return {"error": last_err}
                try:
                    data = json.loads(text)
                except Exception as exc:
                    return {"error": f"Non-JSON: {exc}"}
                return parse_rdap(data)
        except Exception as exc:
            last_err = str(exc)
            if attempt >= retries:
                return {"error": last_err}
            await asyncio.sleep(backoff)
            backoff *= 2
    return {"error": last_err or "unknown"}


# ---------------------------------------------------------------------------
# 输入输出
# ---------------------------------------------------------------------------

def read_domains(csv_path: str) -> list[tuple[str, dict[str, Any]]]:
    """读取明细 CSV，按 domain 去重（保留首次出现），保持顺序。"""
    seen: list[tuple[str, dict[str, Any]]] = []
    seen_set: set[str] = set()
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        if "domain" not in (reader.fieldnames or []):
            raise ValueError("CSV 缺少 domain 列")
        for row in reader:
            domain = (row.get("domain") or "").strip().lower()
            if not domain or domain in seen_set:
                continue
            # 只处理 .com（本工具专为 .com 设计）
            if not domain.endswith(".com"):
                continue
            seen_set.add(domain)
            seen.append((domain, row))
    return seen


def write_output(out_path: str, header: list[str], rows: list[dict[str, Any]]) -> None:
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run_async(
    domains: list[tuple[str, dict[str, Any]]],
    output_csv: str,
    workers: int,
    min_register_days: int,
    registrar: str,
    force: bool,
    db_path: str,
    log: Callable[[str], None],
    should_stop: Callable[[], bool] = lambda: False,
) -> dict[str, int]:
    try:
        import aiohttp
    except ImportError:
        log("[错误] 缺少 aiohttp，请运行: pip install aiohttp")
        return {"total": len(domains), "passed": 0, "failed": 0, "skipped": 0}

    today = date.today()
    conn = open_db(db_path)
    out_rows: list[dict[str, Any]] = []
    stats = {"total": len(domains), "done": 0, "passed": 0, "filtered": 0,
             "failed": 0, "cached": 0, "queried": 0}
    lock = asyncio.Lock()

    # 输入字段 + 新增列
    sample_row = domains[0][1] if domains else {}
    out_fields = list(sample_row.keys()) + [
        "注册时间", "注册天数", "到期时间", "注册商", "注册商ID", "状态", "查询时间",
    ]

    queue: asyncio.Queue = asyncio.Queue()
    for domain, row in domains:
        await queue.put((domain, row))

    async def worker(session: aiohttp.ClientSession) -> None:
        while True:
            if should_stop():
                return
            try:
                domain, row = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                cached = None if force else cache_get(conn, domain)
                if cached and cached.get("ok"):
                    data = cached
                    stats["cached"] += 1
                else:
                    data = await fetch_rdap(session, domain)
                    ok = not data.get("error") and not data.get("not_found")
                    cache_put(conn, domain, data, ok)
                    stats["queried"] += 1

                ok, reason = filter_reason(data, min_register_days, today) if registrar == "juming" \
                    else (True, "ok") if not data.get("error") else (False, "查询失败")

                async with lock:
                    stats["done"] += 1
                    if ok:
                        reg = parse_iso_date(data.get("reg_date"))
                        days = (today - reg).days if reg else ""
                        enriched = dict(row)
                        enriched.update({
                            "注册时间": data.get("reg_date", ""),
                            "注册天数": days,
                            "到期时间": data.get("exp_date", ""),
                            "注册商": data.get("registrar_name", ""),
                            "注册商ID": data.get("registrar_handle", ""),
                            "状态": data.get("status", ""),
                            "查询时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        out_rows.append(enriched)
                        stats["passed"] += 1
                    elif data.get("error") or data.get("not_found"):
                        stats["failed"] += 1
                    else:
                        stats["filtered"] += 1

                    if stats["done"] % 50 == 0 or stats["done"] == stats["total"]:
                        log(f"[进度] {stats['done']}/{stats['total']} "
                            f"通过{stats['passed']} 过滤{stats['filtered']} 失败{stats['failed']} "
                            f"(缓存{stats['cached']} 查询{stats['queried']})")
            finally:
                queue.task_done()

    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rdap+json"}
    connector = aiohttp.TCPConnector(limit=workers, force_close=False)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
        tasks = [asyncio.create_task(worker(session)) for _ in range(workers)]
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            log("[中止] 用户取消，保存已查数据...")
        finally:
            for t in tasks:
                t.cancel()

    if should_stop():
        log("[中止] 已停止，保存已通过结果。")

    write_output(output_csv, out_fields, out_rows)
    conn.close()
    log(f"[完成] 输出: {output_csv}")
    log(f"[统计] 总{stats['total']} 通过{stats['passed']} 过滤{stats['filtered']} "
        f"失败{stats['failed']} (缓存{stats['cached']} 查询{stats['queried']})")
    return {k: stats[k] for k in ("total", "done", "passed", "filtered", "failed", "cached", "queried")}


def run_enrich(
    input_csv: str,
    output_csv: str | None = None,
    workers: int = 15,
    min_register_days: int = 60,
    registrar: str = "juming",
    force: bool = False,
    db_path: str | None = None,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
) -> dict[str, int]:
    """供 web_app 调用的同步入口（内部跑 asyncio）。"""
    input_csv = os.path.abspath(input_csv)
    if not os.path.exists(input_csv):
        raise FileNotFoundError(input_csv)
    if output_csv:
        output_csv = os.path.abspath(output_csv)
    else:
        base, ext = os.path.splitext(input_csv)
        output_csv = f"{base}_enriched{ext}"
    if not db_path:
        # 固定放工具目录，跨天/跨批次共享缓存（域名 whois 基本不变）
        db_path = os.path.join(BASE_DIR, "juming_whois_cache.db")

    domains = read_domains(input_csv)
    if not domains:
        log("[提示] 没有可处理的 .com 域名")
        return {"total": 0, "passed": 0, "filtered": 0, "failed": 0, "cached": 0, "queried": 0}

    log(f"[配置] 输入: {input_csv}")
    log(f"[配置] 输出: {output_csv}")
    log(f"[配置] 唯一 .com 域名: {len(domains)} 个")
    log(f"[配置] 并发: {workers} | 过滤: 注册>={min_register_days}天 且 注册商={registrar}")
    log(f"[配置] 缓存库: {db_path}")

    return asyncio.run(run_async(
        domains, output_csv, workers, min_register_days, registrar,
        force, db_path, log, should_stop,
    ))


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="聚名 .com 域名 RDAP 增强 + 过滤")
    parser.add_argument("input", help="明细 CSV 路径")
    parser.add_argument("-o", "--output", default="", help="输出 CSV 路径（默认加 _enriched）")
    parser.add_argument("--workers", type=int, default=15)
    parser.add_argument("--min-register-days", type=int, default=60)
    parser.add_argument("--registrar", default="juming", help="juming / any / IANA ID")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新查询")
    parser.add_argument("--db", default="", help="缓存库路径")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run_enrich(
            args.input,
            output_csv=args.output or None,
            workers=args.workers,
            min_register_days=args.min_register_days,
            registrar=args.registrar,
            force=args.force,
            db_path=args.db or None,
        )
    except KeyboardInterrupt:
        print("\n[中止] 用户取消")
        return 130
    except Exception as exc:
        print(f"[错误] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
