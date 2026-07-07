#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fetch_gname_ykj_ranges import (
    API_PATH,
    DEFAULT_PROFILE_DIR,
    SALES_URL,
    IncrementalOutputWriter,
    build_fbsj_params,
    build_price_ranges,
    fetch_range_pages,
    launch_context,
    money,
    parse_extra_params,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="gname 一口价网页工具")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class StartRequest(BaseModel):
    ymhz: str = "com,cc,net"
    zcsj_2: str = "2026"
    dqsj_1: str = "30"
    jylx: str = "nei"
    fbsj_days: str = "2"
    ranges: str = "0-20,20.01-30,30.01-40,40.01-50,50.01-60,60.01-70,70.01-80,80.01-90,90.01-100"
    extra: str = ""
    output_dir: str = BASE_DIR
    pagesize: str = "500"
    min_register_days: str = "60"
    import_price_divisor: str = "0.6"
    import_price_multiplier: str = "1.4"
    import_min_price: str = "80"
    export_csv: bool = True
    export_json: bool = False
    export_import_csv: bool = True


class TaskState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.logs: list[str] = []
        self.status = "就绪"
        self.running = False
        self.ready_event = threading.Event()
        self.continue_event = threading.Event()
        self.stop_flag = False
        self.paths: dict[str, str] = {}
        self.count = 0
        self.thread: threading.Thread | None = None

    def reset(self) -> None:
        with self.lock:
            self.logs = []
            self.status = "准备中"
            self.running = True
            self.stop_flag = False
            self.paths = {}
            self.count = 0
        self.ready_event.clear()
        self.continue_event.clear()

    def log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > 3000:
                self.logs = self.logs[-3000:]

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "running": self.running,
                "status": self.status,
                "logs": list(self.logs),
                "paths": dict(self.paths),
                "count": self.count,
            }


state = TaskState()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def parse_decimal_value(value: str, name: str) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"{name} 必须是数字") from exc


def build_args(data: StartRequest) -> argparse.Namespace:
    try:
        pagesize = int((data.pagesize or "500").strip())
    except ValueError as exc:
        raise ValueError("每页数量必须是数字") from exc
    if pagesize < 1 or pagesize > 500:
        raise ValueError("每页数量必须在 1 到 500 之间")

    fbsj_days_text = (data.fbsj_days or "").strip()
    fbsj_days = 0
    if fbsj_days_text:
        try:
            fbsj_days = int(fbsj_days_text)
        except ValueError as exc:
            raise ValueError("发布时间天数必须是数字，或留空") from exc
        if fbsj_days < 1:
            raise ValueError("发布时间天数必须大于 0，或留空")

    try:
        min_register_days = int((data.min_register_days or "60").strip())
    except ValueError as exc:
        raise ValueError("注册天数必须是数字") from exc
    if min_register_days < 0:
        raise ValueError("注册天数不能小于 0")

    import_price_divisor = parse_decimal_value(data.import_price_divisor or "0.6", "价格除数")
    import_price_multiplier = parse_decimal_value(data.import_price_multiplier or "1.4", "价格倍率")
    import_min_price = parse_decimal_value(data.import_min_price or "80", "最低导出价")
    if import_price_divisor <= 0:
        raise ValueError("价格除数必须大于 0")
    if import_price_multiplier <= 0:
        raise ValueError("价格倍率必须大于 0")
    if import_min_price < 0:
        raise ValueError("最低导出价不能小于 0")
    if not any((data.export_csv, data.export_json, data.export_import_csv)):
        raise ValueError("至少需要勾选一种导出文件")

    return argparse.Namespace(
        sales_url=SALES_URL,
        endpoint=API_PATH,
        profile_dir=DEFAULT_PROFILE_DIR,
        browser_channel="chrome",
        cxfs="0",
        ymhz=(data.ymhz or "com,cc,net").strip(),
        zcsj_1="",
        zcsj_2=(data.zcsj_2 or "").strip(),
        dqsj_1=(data.dqsj_1 or "").strip(),
        dqsj_2="",
        jylx=(data.jylx or "nei").strip(),
        fbsj_days=fbsj_days,
        fbsj_start="",
        fbsj_end="",
        fbsj="",
        fbsj_param="fbsj",
        fbsj_separator=" ~ ",
        pagesize=pagesize,
        extra=(data.extra or "").strip(),
        ranges=(data.ranges or "").strip(),
        price_start=Decimal("0"),
        first_end=Decimal("20"),
        price_end=Decimal("100"),
        step=Decimal("10"),
        next_offset=Decimal("0.01"),
        max_pages_per_range=0,
        delay_min=1.2,
        delay_max=2.8,
        retries=2,
        retry_delay=3.0,
        output_dir=(data.output_dir or BASE_DIR).strip() or BASE_DIR,
        output_prefix="gname_ykj_ranges",
        min_register_days=min_register_days,
        import_price_divisor=import_price_divisor,
        import_price_multiplier=import_price_multiplier,
        import_min_price=import_min_price,
        export_csv=data.export_csv,
        export_json=data.export_json,
        export_import_csv=data.export_import_csv,
    )


def manual_fix_handler(code: int | None) -> None:
    if code == -1001:
        state.log("[需要登录] 请在浏览器里重新登录，然后回网页点击“验证完成，继续”。")
        state.set_status("等待登录")
    elif code == -105001:
        state.log("[需要验证] 请在浏览器里完成滑块，然后回网页点击“验证完成，继续”。")
        state.set_status("等待验证")
    else:
        state.log("[需要处理] 请检查浏览器页面，然后回网页点击“验证完成，继续”。")
        state.set_status("等待处理")
    state.continue_event.clear()
    state.continue_event.wait()
    if state.stop_flag:
        raise KeyboardInterrupt


def worker(args: argparse.Namespace) -> None:
    rows: list[dict[str, Any]] = []
    output_writer: IncrementalOutputWriter | None = None
    exit_code = 0
    try:
        from playwright.sync_api import sync_playwright

        ranges = build_price_ranges(args)
        fbsj_params = build_fbsj_params(args)
        extra_params = parse_extra_params(args.extra)
        output_writer = IncrementalOutputWriter(args)

        state.log("[配置] 价格区间: " + ", ".join(f"{money(a)}-{money(b)}" for a, b in ranges))
        state.log(f"[配置] 后缀: {args.ymhz}")
        state.log(f"[配置] 每页数量: {args.pagesize}")
        state.log(f"[配置] 注册天数大于: {args.min_register_days}")
        state.log(f"[配置] 0612导出价: 真实价格 / {args.import_price_divisor} * {args.import_price_multiplier}，最低 {args.import_min_price}")
        state.log("[配置] 发布时间: " + ("&".join(f"{k}={v}" for k, v in fbsj_params.items()) if fbsj_params else "全部"))
        if output_writer.csv_path:
            state.log(f"[保存] CSV: {output_writer.csv_path}")
        if output_writer.jsonl_path:
            state.log(f"[保存] JSONL: {output_writer.jsonl_path}")
        if output_writer.import_csv_path:
            state.log(f"[保存] 0612格式CSV: {output_writer.import_csv_path}")

        with sync_playwright() as pw:
            context = launch_context(pw, args)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.sales_url, wait_until="domcontentloaded")
                state.log("[等待] 浏览器已打开，请登录 gname 并完成滑块验证。")
                state.set_status("等待登录/验证")
                state.ready_event.wait()
                if state.stop_flag:
                    raise KeyboardInterrupt
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                for index, (price_min, price_max) in enumerate(ranges, 1):
                    if state.stop_flag:
                        raise KeyboardInterrupt
                    state.set_status(f"获取中 {index}/{len(ranges)}")
                    state.log(f"\n[区间 {index}/{len(ranges)}] {money(price_min)} - {money(price_max)}")
                    fetch_range_pages(
                        page,
                        args,
                        extra_params,
                        fbsj_params,
                        price_min,
                        price_max,
                        rows,
                        log=state.log,
                        manual_fix_handler=manual_fix_handler,
                        output_writer=output_writer,
                        should_stop=lambda: state.stop_flag,
                    )

            finally:
                context.close()
    except KeyboardInterrupt:
        state.log("[中止] 正在保存已有数据。")
        exit_code = 130
    except Exception as exc:
        state.log(f"[错误] {exc}")
        exit_code = 1

    try:
        if output_writer:
            paths = output_writer.finalize()
        else:
            output_writer = IncrementalOutputWriter(args)
            paths = output_writer.finalize()
        with state.lock:
            state.paths = paths
            state.count = len(rows)
        state.log("\n[完成] 已保存结果")
        state.log(f"[记录] {len(rows)} 条")
        if paths.get("csv"):
            state.log(f"[CSV] {paths['csv']}")
        if paths.get("json"):
            state.log(f"[JSON] {paths['json']}")
        if paths.get("import_csv"):
            state.log(f"[0612格式CSV] {paths['import_csv']}")
    except Exception as exc:
        state.log(f"[错误] 保存失败: {exc}")
        exit_code = 1

    with state.lock:
        state.running = False
        state.status = "完成" if exit_code == 0 else "已停止/出错"


@app.post("/api/start")
def start(data: StartRequest) -> dict[str, Any]:
    with state.lock:
        if state.running:
            raise HTTPException(status_code=409, detail="已有任务正在运行")
    try:
        args = build_args(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state.reset()
    thread = threading.Thread(target=worker, args=(args,), daemon=True)
    with state.lock:
        state.thread = thread
    thread.start()
    return {"ok": True}


@app.post("/api/ready")
def ready() -> dict[str, Any]:
    state.ready_event.set()
    state.log("[操作] 已确认登录/验证完成，开始获取。")
    return {"ok": True}


@app.post("/api/continue")
def continue_task() -> dict[str, Any]:
    state.continue_event.set()
    state.log("[操作] 已确认验证完成，继续当前请求。")
    return {"ok": True}


@app.post("/api/stop")
def stop() -> dict[str, Any]:
    state.stop_flag = True
    state.ready_event.set()
    state.continue_event.set()
    state.log("[停止] 已请求停止，当前请求结束后保存已有数据。")
    return {"ok": True}


@app.get("/api/status")
def status() -> dict[str, Any]:
    return state.snapshot()
