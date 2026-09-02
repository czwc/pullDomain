#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚名网一口价抓取 - 网页控制台（FastAPI）。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from fetch_juming_ykj import (
    API_PATH,
    DEFAULT_PROFILE_DIR,
    SALES_URL,
    IncrementalOutputWriter,
    fetch_range_pages,
    launch_context,
    money,
    parse_extra_params,
    parse_range_spec,
)
import enrich_whois

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_PATH = os.path.join(BASE_DIR, "web_config.json")
PROFILES_PATH = os.path.join(BASE_DIR, "web_profiles.json")

app = FastAPI(title="聚名网一口价网页工具")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class StartRequest(BaseModel):
    ymhz: str = ".com"
    dqsj_1: str = "30"
    pxsj: str = "1"
    jgpx: str = "38"
    jgpx2: str = "1"
    psize: str = "500"
    ranges: str = "0-100"
    extra: str = ""
    output_dir: str = BASE_DIR
    import_price_divisor: str = "0.6"
    import_price_multiplier: str = "1.4"
    import_min_price: str = "80"
    export_csv: bool = True
    export_json: bool = False
    export_import_csv: bool = True


def model_to_dict(model: BaseModel) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    return model.dict()


def read_saved_config() -> dict[str, Any] | None:
    """读取上次保存的参数；没有保存过或文件损坏时返回 None。"""
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
            saved = json.load(fp)
    except Exception:
        return None
    if not isinstance(saved, dict):
        return None

    merged = model_to_dict(StartRequest())
    for key, default_value in merged.items():
        if key not in saved:
            continue
        value = saved[key]
        if isinstance(default_value, bool):
            if isinstance(value, bool):
                merged[key] = value
            else:
                merged[key] = str(value).strip().lower() in {"1", "true", "yes", "on"}
        else:
            merged[key] = "" if value is None else str(value)
    return merged


def write_saved_config(data: StartRequest) -> None:
    """先写临时文件再替换，避免中途出错把配置写坏。"""
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(model_to_dict(data), fp, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def read_profiles() -> dict[str, Any]:
    if not os.path.exists(PROFILES_PATH):
        return {}
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def write_profiles(data: dict[str, Any]) -> None:
    tmp_path = PROFILES_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp_path, PROFILES_PATH)


class SaveProfileRequest(BaseModel):
    name: str
    config: dict[str, Any] = {}


class ProfileNameRequest(BaseModel):
    name: str


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
        psize = int((data.psize or "500").strip())
    except ValueError as exc:
        raise ValueError("每页数量必须是数字") from exc
    if psize < 1 or psize > 500:
        raise ValueError("每页数量必须在 1 到 500 之间")

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

    try:
        parse_range_spec(data.ranges or "0-100")
    except ValueError as exc:
        raise ValueError(f"价格区间格式有误: {exc}") from exc

    return argparse.Namespace(
        sales_url=SALES_URL,
        endpoint=API_PATH,
        profile_dir=DEFAULT_PROFILE_DIR,
        browser_channel="chrome",
        pxsj=(data.pxsj or "1").strip(),
        ymhz=(data.ymhz or ".com").strip(),
        dqsj_1=(data.dqsj_1 or "").strip(),
        psize=psize,
        jgpx=(data.jgpx or "38").strip(),
        jgpx2=(data.jgpx2 or "1").strip(),
        ranges=(data.ranges or "0-100").strip(),
        extra=(data.extra or "").strip(),
        delay_min=2.0,
        delay_max=4.0,
        retries=3,
        retry_delay=5.0,
        output_dir=(data.output_dir or BASE_DIR).strip() or BASE_DIR,
        output_prefix="juming_ykj_ranges",
        import_price_divisor=import_price_divisor,
        import_price_multiplier=import_price_multiplier,
        import_min_price=import_min_price,
        export_csv=data.export_csv,
        export_json=data.export_json,
        export_import_csv=data.export_import_csv,
    )


def manual_fix_handler(code: int | None, msg: str) -> None:
    if code == -401:
        state.log("[需要验证] 聚名网触发滑块验证，请在浏览器里完成滑块，然后回网页点击“验证完成，继续”。")
        state.set_status("等待验证")
    elif "登录" in msg:
        state.log("[需要登录] 请在浏览器里重新登录，然后回网页点击“验证完成，继续”。")
        state.set_status("等待登录")
    else:
        state.log(f"[需要处理] code={code} msg={msg}，请检查浏览器页面，然后回网页点击“验证完成，继续”。")
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

        ranges = parse_range_spec(args.ranges)
        extra_params = parse_extra_params(args.extra)
        output_writer = IncrementalOutputWriter(args)

        state.log("[配置] 价格区间: " + ", ".join(f"{money(a)}-{money(b)}" for a, b in ranges))
        state.log(f"[配置] 后缀: {args.ymhz}")
        state.log(f"[配置] 每页数量: {args.psize}")
        state.log(f"[配置] 排序: jgpx={args.jgpx} jgpx2={args.jgpx2}")
        state.log(f"[配置] 导出价: 真实价格 / {args.import_price_divisor} * {args.import_price_multiplier}，最低 {args.import_min_price}")
        if output_writer.csv_path:
            state.log(f"[保存] CSV: {output_writer.csv_path}")
        if output_writer.import_csv_path:
            state.log(f"[保存] 导入CSV: {output_writer.import_csv_path}")
        if output_writer.jsonl_path:
            state.log(f"[保存] JSONL: {output_writer.jsonl_path}")

        with sync_playwright() as pw:
            context = launch_context(pw, args)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.sales_url, wait_until="domcontentloaded")
                state.log("[等待] 浏览器已打开聚名网，请登录并完成滑块验证。")
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
            state.log(f"[导入CSV] {paths['import_csv']}")
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


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    saved = read_saved_config()
    return {
        "saved": saved is not None,
        "config": saved if saved is not None else model_to_dict(StartRequest()),
    }


@app.post("/api/config")
def save_config(data: StartRequest) -> dict[str, Any]:
    try:
        write_saved_config(data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存参数失败: {exc}") from exc
    return {"ok": True, "path": CONFIG_PATH}


@app.post("/api/config/reset")
def reset_config() -> dict[str, Any]:
    try:
        if os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"清除参数失败: {exc}") from exc
    return {"ok": True}


@app.get("/api/profiles")
def list_profiles() -> dict[str, Any]:
    profiles = read_profiles()
    return {"profiles": list(profiles.keys())}


@app.post("/api/profiles/save")
def save_profile(req: SaveProfileRequest) -> dict[str, Any]:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="方案名称不能为空")
    profiles = read_profiles()
    profiles[name] = {
        "config": req.config,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        write_profiles(profiles)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存方案失败: {exc}") from exc
    return {"ok": True}


@app.post("/api/profiles/load")
def load_profile(req: ProfileNameRequest) -> dict[str, Any]:
    name = req.name.strip()
    profiles = read_profiles()
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"方案不存在: {name}")
    return {"config": profiles[name].get("config", {})}


@app.post("/api/profiles/delete")
def delete_profile(req: ProfileNameRequest) -> dict[str, Any]:
    name = req.name.strip()
    profiles = read_profiles()
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"方案不存在: {name}")
    del profiles[name]
    try:
        write_profiles(profiles)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除方案失败: {exc}") from exc
    return {"ok": True}


class RevealRequest(BaseModel):
    path: str = ""


# ---------------------------------------------------------------------------
# WHOIS 增强（RDAP）
# ---------------------------------------------------------------------------

class EnrichRequest(BaseModel):
    path: str
    min_register_days: int = 60
    registrar: str = "juming"
    workers: int = 15
    force: bool = False


enrich_state = TaskState()


def enrich_worker(req: EnrichRequest) -> None:
    exit_code = 0
    try:
        stats = enrich_whois.run_enrich(
            req.path,
            output_csv=None,
            workers=req.workers,
            min_register_days=req.min_register_days,
            registrar=req.registrar,
            force=req.force,
            db_path=None,
            log=enrich_state.log,
            should_stop=lambda: enrich_state.stop_flag,
        )
        with enrich_state.lock:
            enrich_state.count = stats.get("passed", 0)
        stats_msg = f"[结果] 通过 {stats.get('passed', 0)} / 总 {stats.get('total', 0)} (过滤 {stats.get('filtered', 0)} 失败 {stats.get('failed', 0)})"
        enrich_state.log(stats_msg)
    except Exception as exc:
        enrich_state.log(f"[错误] {exc}")
        exit_code = 1

    with enrich_state.lock:
        enrich_state.running = False
        enrich_state.status = "完成" if exit_code == 0 else "出错"


@app.post("/api/enrich/start")
def enrich_start(req: EnrichRequest) -> dict[str, Any]:
    with enrich_state.lock:
        if enrich_state.running:
            raise HTTPException(status_code=409, detail="已有增强任务在运行")
    path = (req.path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="缺少 CSV 路径")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

    enrich_state.reset()
    enrich_state.set_status("增强中")
    enrich_state.log(f"[开始] 增强 {path}")
    thread = threading.Thread(target=enrich_worker, args=(req,), daemon=True)
    with enrich_state.lock:
        enrich_state.thread = thread
    thread.start()
    return {"ok": True}


@app.get("/api/enrich/status")
def enrich_status() -> dict[str, Any]:
    return enrich_state.snapshot()


@app.post("/api/enrich/stop")
def enrich_stop() -> dict[str, Any]:
    enrich_state.stop_flag = True
    enrich_state.log("[停止] 已请求停止，当前查询结束后保存。")
    return {"ok": True}


@app.post("/api/reveal")
def reveal(data: RevealRequest) -> dict[str, Any]:
    path = (data.path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="缺少文件路径")

    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {abs_path}")

    try:
        if sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", abs_path])
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", abs_path])
        else:
            subprocess.run(["xdg-open", os.path.dirname(abs_path)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"打开失败: {exc}") from exc

    return {"ok": True}
