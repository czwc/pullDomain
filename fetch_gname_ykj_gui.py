#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import threading
import tkinter as tk
from datetime import datetime
from decimal import Decimal, InvalidOperation


from tkinter import filedialog, messagebox, scrolledtext, ttk

from fetch_gname_ykj_ranges import (
    SALES_URL,
    API_PATH,
    DEFAULT_PROFILE_DIR,
    build_fbsj_params,
    build_price_ranges,
    fetch_range_pages,
    IncrementalOutputWriter,
    launch_context,
    money,
    parse_extra_params,
)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("gname 一口价数据获取")
        self.minsize(920, 780)
        self._center(980, 820)


        self._ready_event = threading.Event()
        self._continue_event = threading.Event()
        self._stop_flag = False
        self._worker_thread: threading.Thread | None = None
        self._build_ui()

    def _center(self, width: int, height: int) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")

    def _build_ui(self) -> None:
        self.configure(bg="#f4f6f8")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", padding=6)
        style.configure("Primary.TButton", padding=7)
        style.configure("TEntry", padding=4)

        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text="gname 一口价数据获取", font=("Microsoft YaHei UI", 15, "bold")).pack(
            side="left"
        )
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        form = ttk.LabelFrame(root, text="查询参数", padding=12)
        form.pack(fill="x", pady=(12, 8))

        self.ymhz_var = tk.StringVar(value="com,cc,net")
        self.zcsj2_var = tk.StringVar(value="2026")
        self.dqsj1_var = tk.StringVar(value="30")
        self.jylx_var = tk.StringVar(value="nei")
        self.days_var = tk.StringVar(value="2")
        self.ranges_var = tk.StringVar(value="0-20,20.01-30,30.01-40,40.01-50,50.01-60,60.01-70,70.01-80,80.01-90,90.01-100")
        self.output_dir_var = tk.StringVar(value=os.getcwd())
        self.extra_var = tk.StringVar(value="")
        self.pagesize_var = tk.StringVar(value="500")
        self.min_register_days_var = tk.StringVar(value="60")
        self.import_price_divisor_var = tk.StringVar(value="0.6")
        self.import_price_multiplier_var = tk.StringVar(value="1.4")
        self.import_min_price_var = tk.StringVar(value="80")
        self.export_csv_var = tk.BooleanVar(value=True)
        self.export_json_var = tk.BooleanVar(value=False)
        self.export_import_csv_var = tk.BooleanVar(value=True)


        self._row(form, 0, "后缀 ymhz", self.ymhz_var, "com,cc,net")

        self._row(form, 1, "注册年份 zcsj_2", self.zcsj2_var, "默认 2026")
        self._row(form, 2, "到期天数 dqsj_1", self.dqsj1_var, "30")
        self._row(form, 3, "交易类型 jylx", self.jylx_var, "nei")
        self._row(form, 4, "发布时间天数", self.days_var, "默认 2=今天和昨天；留空=全部")
        self._row(form, 5, "价格区间", self.ranges_var, "可自行删减")

        self._row(form, 6, "额外参数", self.extra_var, "key=value&key2=value2")

        ttk.Label(form, text="输出目录").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.output_dir_var).grid(row=7, column=1, sticky="ew", pady=5, padx=8)
        ttk.Button(form, text="浏览", command=self._pick_output_dir).grid(row=7, column=2, sticky="ew")

        ttk.Label(form, text="每页数量").grid(row=8, column=0, sticky="w", pady=5)
        ttk.Entry(form, textvariable=self.pagesize_var).grid(row=8, column=1, sticky="ew", pady=5, padx=8)
        ttk.Label(form, text="最大 500").grid(row=8, column=2, sticky="w")

        self._row(form, 9, "注册天数大于", self.min_register_days_var, "0=不过滤；默认 60")
        self._row(form, 10, "价格除数", self.import_price_divisor_var, "导出价=真实价格÷除数×倍率")
        self._row(form, 11, "价格倍率", self.import_price_multiplier_var, "默认 1.4")
        self._row(form, 12, "最低导出价", self.import_min_price_var, "默认 80")

        export_frame = ttk.Frame(form)
        export_frame.grid(row=13, column=1, sticky="w", pady=5, padx=8)
        ttk.Label(form, text="导出文件").grid(row=13, column=0, sticky="w", pady=5)
        ttk.Checkbutton(export_frame, text="主CSV", variable=self.export_csv_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(export_frame, text="JSON", variable=self.export_json_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(export_frame, text="0612格式CSV", variable=self.export_import_csv_var).pack(side="left")
        ttk.Label(form, text="至少勾选一个").grid(row=13, column=2, sticky="w")

        form.columnconfigure(1, weight=1)


        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(4, 8))
        self.start_btn = ttk.Button(actions, text="启动浏览器", command=self._start, style="Primary.TButton")
        self.start_btn.pack(side="left", padx=(0, 8))
        self.ready_btn = ttk.Button(actions, text="登录/验证完成，开始获取", command=self._signal_ready, state="disabled")
        self.ready_btn.pack(side="left", padx=4)
        self.continue_btn = ttk.Button(actions, text="验证完成，继续", command=self._signal_continue, state="disabled")
        self.continue_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(actions, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(root, mode="indeterminate")
        self.progress.pack(fill="x", pady=(2, 8))

        log_box = ttk.LabelFrame(root, text="日志", padding=8)
        log_box.pack(fill="both", expand=True)
        self.log = scrolledtext.ScrolledText(log_box, height=18, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    def _row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, hint: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=5, padx=8)
        ttk.Label(parent, text=hint).grid(row=row, column=2, sticky="w")

    def _pick_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir_var.set(path)

    def _log(self, message: str) -> None:
        def write() -> None:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log.configure(state="normal")
            self.log.insert("end", f"[{timestamp}] {message}\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        self.after(0, write)


    def _set_status(self, message: str) -> None:
        self.after(0, lambda: self.status_var.set(message))

    def _set_buttons(self, running: bool) -> None:
        def update() -> None:
            self.start_btn.configure(state="disabled" if running else "normal")
            self.stop_btn.configure(state="normal" if running else "disabled")
            if not running:
                self.ready_btn.configure(state="disabled")
                self.continue_btn.configure(state="disabled")

        self.after(0, update)

    def _signal_ready(self) -> None:
        self._ready_event.set()
        self.ready_btn.configure(state="disabled")

    def _signal_continue(self) -> None:
        self._continue_event.set()
        self.continue_btn.configure(state="disabled")

    def _stop(self) -> None:
        self._stop_flag = True
        self._ready_event.set()
        self._continue_event.set()
        self._log("[停止] 已请求停止，当前请求结束后保存已有数据。")

    def _start(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return

        try:
            args = self._build_args()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self._stop_flag = False
        self._ready_event.clear()
        self._continue_event.clear()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        self._set_buttons(True)
        self.progress.start(10)
        self._worker_thread = threading.Thread(target=self._worker, args=(args,), daemon=True)
        self._worker_thread.start()

    def _build_args(self) -> argparse.Namespace:
        pagesize_text = self.pagesize_var.get().strip() or "500"
        try:
            pagesize = int(pagesize_text)
        except ValueError as exc:
            raise ValueError("每页数量必须是数字") from exc
        if pagesize < 1 or pagesize > 500:
            raise ValueError("每页数量必须在 1 到 500 之间")

        days_text = self.days_var.get().strip()
        fbsj_days = 0
        if days_text:
            try:
                fbsj_days = int(days_text)
            except ValueError as exc:
                raise ValueError("发布时间天数必须是数字，或留空") from exc
            if fbsj_days < 1:
                raise ValueError("发布时间天数必须大于 0，或留空")

        try:
            min_register_days = int(self.min_register_days_var.get().strip() or "60")
        except ValueError as exc:
            raise ValueError("注册天数必须是数字") from exc
        if min_register_days < 0:
            raise ValueError("注册天数不能小于 0")

        try:
            import_price_divisor = Decimal(self.import_price_divisor_var.get().strip() or "0.6")
            import_price_multiplier = Decimal(self.import_price_multiplier_var.get().strip() or "1.4")
            import_min_price = Decimal(self.import_min_price_var.get().strip() or "80")
        except InvalidOperation as exc:
            raise ValueError("价格公式参数必须是数字") from exc
        if import_price_divisor <= 0:
            raise ValueError("价格除数必须大于 0")
        if import_price_multiplier <= 0:
            raise ValueError("价格倍率必须大于 0")
        if import_min_price < 0:
            raise ValueError("最低导出价不能小于 0")

        export_csv = self.export_csv_var.get()
        export_json = self.export_json_var.get()
        export_import_csv = self.export_import_csv_var.get()
        if not any((export_csv, export_json, export_import_csv)):
            raise ValueError("至少需要勾选一种导出文件")

        return argparse.Namespace(

            sales_url=SALES_URL,
            endpoint=API_PATH,
            profile_dir=DEFAULT_PROFILE_DIR,
            browser_channel="chrome",
            cxfs="0",
            ymhz=self.ymhz_var.get().strip() or "com,cc,net",
            zcsj_1="",
            zcsj_2=self.zcsj2_var.get().strip(),
            dqsj_1=self.dqsj1_var.get().strip(),
            dqsj_2="",
            jylx=self.jylx_var.get().strip() or "nei",
            fbsj_days=fbsj_days,
            fbsj_start="",
            fbsj_end="",
            fbsj="",
            fbsj_param="fbsj",
            fbsj_separator=" ~ ",
            pagesize=pagesize,
            extra=self.extra_var.get().strip(),
            ranges=self.ranges_var.get().strip(),
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
            output_dir=self.output_dir_var.get().strip() or ".",
            output_prefix="gname_ykj_ranges",
            min_register_days=min_register_days,
            import_price_divisor=import_price_divisor,
            import_price_multiplier=import_price_multiplier,
            import_min_price=import_min_price,
            export_csv=export_csv,
            export_json=export_json,
            export_import_csv=export_import_csv,
        )


    def _manual_fix_handler(self, code: int | None) -> None:
        if code == -1001:
            self._log("[需要登录] 请在浏览器里重新登录，然后点击“验证完成，继续”。")
            self._set_status("等待登录")
        elif code == -105001:
            self._log("[需要验证] 请在浏览器里完成滑块，然后点击“验证完成，继续”。")
            self._set_status("等待验证")
        else:
            self._log("[需要处理] 请检查浏览器页面，然后点击“验证完成，继续”。")
            self._set_status("等待处理")

        self._continue_event.clear()
        self.after(0, lambda: self.continue_btn.configure(state="normal"))
        self._continue_event.wait()
        if self._stop_flag:
            raise KeyboardInterrupt

    def _worker(self, args: argparse.Namespace) -> None:
        rows: list[dict] = []
        output_writer: IncrementalOutputWriter | None = None
        exit_code = 0
        try:
            from playwright.sync_api import sync_playwright

            ranges = build_price_ranges(args)
            fbsj_params = build_fbsj_params(args)
            extra_params = parse_extra_params(args.extra)
            output_writer = IncrementalOutputWriter(args)

            self._log("[配置] 价格区间: " + ", ".join(f"{money(a)}-{money(b)}" for a, b in ranges))
            self._log(f"[配置] 后缀: {args.ymhz}")
            self._log(f"[配置] 每页数量: {args.pagesize}")
            self._log(f"[配置] 注册天数大于: {args.min_register_days}")

            self._log(
                f"[配置] 0612导出价: 真实价格 / {args.import_price_divisor} * {args.import_price_multiplier}，最低 {args.import_min_price}"
            )
            self._log("[配置] 发布时间: " + ("&".join(f"{k}={v}" for k, v in fbsj_params.items()) if fbsj_params else "全部"))
            if output_writer.csv_path:
                self._log(f"[保存] CSV: {output_writer.csv_path}")
            if output_writer.jsonl_path:
                self._log(f"[保存] JSONL: {output_writer.jsonl_path}")
            if output_writer.import_csv_path:
                self._log(f"[保存] 0612格式CSV: {output_writer.import_csv_path}")


            with sync_playwright() as pw:
                context = launch_context(pw, args)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(args.sales_url, wait_until="domcontentloaded")

                    self._log("[等待] 浏览器已打开，请登录 gname 并完成滑块验证。")
                    self._set_status("等待登录/验证")
                    self.after(0, lambda: self.ready_btn.configure(state="normal"))
                    self._ready_event.wait()
                    if self._stop_flag:
                        raise KeyboardInterrupt
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=10000)
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                    for index, (price_min, price_max) in enumerate(ranges, 1):
                        if self._stop_flag:
                            raise KeyboardInterrupt
                        self._set_status(f"获取中 {index}/{len(ranges)}")
                        self._log(f"\n[区间 {index}/{len(ranges)}] {money(price_min)} - {money(price_max)}")
                        fetch_range_pages(
                            page,
                            args,
                            extra_params,
                            fbsj_params,
                            price_min,
                            price_max,
                            rows,
                            log=self._log,
                            manual_fix_handler=self._manual_fix_handler,
                            output_writer=output_writer,
                        )
                finally:
                    context.close()
        except KeyboardInterrupt:
            self._log("[中止] 正在保存已有数据。")
            exit_code = 130
        except Exception as exc:
            self._log(f"[错误] {exc}")
            exit_code = 1

        try:
            if output_writer:
                paths = output_writer.finalize()
            else:
                output_writer = IncrementalOutputWriter(args)
                paths = output_writer.finalize()
            self._log("\n[完成] 已保存结果")
            self._log(f"[记录] {len(rows)} 条")
            if paths.get("csv"):
                self._log(f"[CSV] {paths['csv']}")
            if paths.get("json"):
                self._log(f"[JSON] {paths['json']}")
            if paths.get("import_csv"):
                self._log(f"[0612格式CSV] {paths['import_csv']}")


        except Exception as exc:
            self._log(f"[错误] 保存失败: {exc}")
            exit_code = 1

        self.progress.stop()
        self._set_status("完成" if exit_code == 0 else "已停止/出错")
        self._set_buttons(False)


if __name__ == "__main__":
    App().mainloop()
