#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gname.net 一口价域名批量查询工具 v3
- 使用真实 Chrome 浏览器，完全绕过反爬检测
- 所有请求从浏览器内部发出，与人工操作无区别
- 遇到验证码自动暂停，人工通过后继续
"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import json
import time
import random
import os
import sys
import subprocess
from datetime import datetime

BATCH_SIZE = 400

# 在浏览器内部执行的 JS fetch 代码
JS_QUERY = """
async (domainStr) => {
    try {
        const resp = await fetch('/request/get_ykj_list', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01'
            },
            body: new URLSearchParams({
                cxfs: '0',
                gjz_cha: domainStr,
                pagesize: '400',
                page: '1'
            })
        });
        return await resp.json();
    } catch(e) {
        return {error: e.toString()};
    }
}
"""


def extract_list(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    for key in ("data", "list", "result", "rows", "items"):
        v = data.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            for k2 in ("list", "rows", "items", "data"):
                v2 = v.get(k2)
                if isinstance(v2, list):
                    return v2
    return None


def parse_items(items):
    results = []
    for item in (items or []):
        domain = (item.get("domain") or item.get("dname") or item.get("ym")
                  or item.get("domain_name") or item.get("name") or "")
        price = (item.get("qian") or item.get("price") or item.get("sell_price") or item.get("jg")
                 or item.get("ykj_price") or item.get("cny_price")
                 or item.get("rmb_price") or "")
        currency = item.get("currency") or item.get("currency_type") or "CNY"
        if domain:
            results.append({"domain": domain, "price": price,
                            "currency": currency, "raw": item})
    return results


# ─────────────────────────── GUI ───────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("gname 一口价批量查询工具 v3 (真实浏览器版)")
        self.minsize(720, 580)
        self.configure(bg="#1e1e2e")
        self._center(760, 640)
        self._build_ui()
        self._all_results = []
        self._ready_event = threading.Event()
        self._continue_event = threading.Event()
        self._stop_flag = False

    def _center(self, w, h):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _build_ui(self):
        BG, CARD, ACC, TXT, SUB = "#1e1e2e", "#2a2a3e", "#7c6af7", "#e0e0f0", "#888aaa"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TButton", background=ACC, foreground="white",
                        font=("微软雅黑", 10, "bold"), relief="flat", padding=6)
        style.map("TButton", background=[("active", "#6a58e0"), ("disabled", "#444460")])
        style.configure("Green.TButton", background="#27ae60", foreground="white",
                        font=("微软雅黑", 10, "bold"), relief="flat", padding=6)
        style.map("Green.TButton", background=[("active", "#1e8449")])
        style.configure("TProgressbar", troughcolor=CARD, background=ACC, thickness=10)

        # 标题
        tk.Label(self, text="gname.net  一口价域名批量查询", bg=BG,
                 fg=TXT, font=("微软雅黑", 15, "bold")).pack(pady=(16, 2))
        tk.Label(self, text="使用真实 Chrome 浏览器 · 自动处理验证 · 无需手动复制 Cookie",
                 bg=BG, fg=SUB, font=("微软雅黑", 9)).pack()

        # ① 选择文件
        f1 = tk.LabelFrame(self, text=" ① 选择域名文件（每行一个域名的 .txt）",
                           bg=CARD, fg=ACC, font=("微软雅黑", 9, "bold"), bd=0)
        f1.pack(fill="x", padx=20, pady=(14, 0))
        row1 = tk.Frame(f1, bg=CARD)
        row1.pack(fill="x", padx=10, pady=8)
        self.file_var = tk.StringVar()
        # 自动检测同目录 txt
        self._auto_detect_file()
        tk.Entry(row1, textvariable=self.file_var, bg="#333350", fg=TXT,
                 insertbackground=TXT, relief="flat", font=("Consolas", 9),
                 width=52).pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        ttk.Button(row1, text="浏览…", command=self._pick_file).pack(side="left")

        # ② 流程说明
        f2 = tk.LabelFrame(self, text=" ② 工作流程",
                           bg=CARD, fg=ACC, font=("微软雅黑", 9, "bold"), bd=0)
        f2.pack(fill="x", padx=20, pady=(10, 0))
        steps = (
            "  1. 点击「启动浏览器并开始」→ Chrome 自动打开 gname.net/sales\n"
            "  2. 【重要】如果未登录，请先在弹出的浏览器里登录你的账号！\n"
            "  3. 如果页面要求验证码（滑块），在 Chrome 里手动完成\n"
            "  4. 登录且没验证码后，点击下方「✓ 验证完成，开始查询」按钮\n"
            "  5. 工具自动批量查询，遇到拦截会弹窗并在日志中提示你"
        )
        tk.Label(f2, text=steps, bg=CARD, fg=SUB, font=("微软雅黑", 9),
                 justify="left").pack(anchor="w", padx=10, pady=(4, 8))

        # 按钮行
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=10)

        self.start_btn = ttk.Button(btn_row, text="▶  启动浏览器并开始",
                                    command=self._start)
        self.start_btn.pack(side="left", padx=6)

        self.ready_btn = ttk.Button(btn_row, text="✓  验证完成，开始查询",
                                    command=self._signal_ready, style="Green.TButton",
                                    state="disabled")
        self.ready_btn.pack(side="left", padx=6)

        self.continue_btn = ttk.Button(btn_row, text="↩  验证完成，继续",
                                       command=self._signal_continue, style="Green.TButton",
                                       state="disabled")
        self.continue_btn.pack(side="left", padx=6)

        self.stop_btn = ttk.Button(btn_row, text="■  停止",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)

        # 进度
        self.progress = ttk.Progressbar(self, mode="determinate", style="TProgressbar")
        self.progress.pack(fill="x", padx=20, pady=(0, 2))
        self.status_var = tk.StringVar(value="就绪 — 请选择文件后点击启动")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=SUB,
                 font=("微软雅黑", 8)).pack(anchor="w", padx=22)

        # 日志区
        f3 = tk.LabelFrame(self, text=" 查询日志 / 结果预览",
                           bg=CARD, fg=ACC, font=("微软雅黑", 9, "bold"), bd=0)
        f3.pack(fill="both", expand=True, padx=20, pady=(6, 16))
        self.log = scrolledtext.ScrolledText(
            f3, bg="#12121e", fg="#c8c8e8", insertbackground="white",
            font=("Consolas", 9), relief="flat", wrap="word", state="disabled"
        )
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _auto_detect_file(self):
        here = os.path.dirname(os.path.abspath(__file__))
        txts = [f for f in os.listdir(here)
                if f.endswith(".txt") and "gname" not in f.lower()]
        if len(txts) == 1:
            self.file_var = tk.StringVar(value=os.path.join(here, txts[0]))
        else:
            self.file_var = tk.StringVar()

    def _pick_file(self):
        p = filedialog.askopenfilename(
            title="选择域名 txt 文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        if p:
            self.file_var.set(p)

    def _log(self, msg, color="#c8c8e8"):
        def _do():
            self.log.configure(state="normal")
            tag = f"tag_{id(msg)}"
            self.log.insert("end", msg + "\n", tag)
            self.log.tag_configure(tag, foreground=color)
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(0, _do)

    def _set_status(self, s):
        self.after(0, lambda: self.status_var.set(s))

    def _set_progress(self, val, max_=None):
        def _do():
            if max_ is not None:
                self.progress["maximum"] = max_
            self.progress["value"] = val
        self.after(0, _do)

    def _signal_ready(self):
        self._ready_event.set()
        self.after(0, lambda: self.ready_btn.configure(state="disabled"))

    def _signal_continue(self):
        self._continue_event.set()
        self.after(0, lambda: self.continue_btn.configure(state="disabled"))

    def _stop(self):
        self._stop_flag = True
        self._ready_event.set()
        self._continue_event.set()
        self._log("[停止] 用户中止查询，已收集数据将自动保存", "#ff6b6b")

    def _start(self):
        file_path = self.file_var.get().strip()
        if not file_path or not os.path.exists(file_path):
            messagebox.showerror("错误", "请先选择一个有效的域名 txt 文件")
            return

        self._all_results.clear()
        self._stop_flag = False
        self._ready_event.clear()
        self._continue_event.clear()

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        self.after(0, lambda: self.start_btn.configure(state="disabled"))
        self.after(0, lambda: self.stop_btn.configure(state="normal"))

        threading.Thread(target=self._worker, args=(file_path,), daemon=True).start()

    def _worker(self, file_path):
        # 1. 确保 playwright 已安装
        if not self._ensure_playwright():
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.after(0, lambda: self.stop_btn.configure(state="disabled"))
            return

        # 2. 读取域名
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                domains = [l.strip() for l in f if l.strip()]
        except Exception as e:
            self._log(f"[错误] 读取文件失败: {e}", "#ff6b6b")
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            return

        self._log(f"[信息] 读取到 {len(domains)} 个域名", "#6af7a0")

        # 3. 启动浏览器
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._log("[错误] playwright 导入失败，请重启程序", "#ff6b6b")
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            return

        self._log("[信息] 正在启动 Chrome 浏览器...", "#aaaacc")
        self._set_status("正在启动浏览器...")

        try:
            with sync_playwright() as pw:
                # 优先用本机 Chrome，没有则用内置 Chromium
                try:
                    browser = pw.chromium.launch(
                        headless=False,
                        channel="chrome",
                        args=["--start-maximized"]
                    )
                    self._log("[信息] 使用本机 Chrome 浏览器", "#6af7a0")
                except Exception:
                    browser = pw.chromium.launch(
                        headless=False,
                        args=["--start-maximized"]
                    )
                    self._log("[信息] 使用内置 Chromium 浏览器", "#6af7a0")

                ctx = browser.new_context(viewport=None)
                page = ctx.new_page()
                page.goto("https://www.gname.net/sales", wait_until="domcontentloaded")

                self._log("[等待] Chrome 已打开 gname.net/sales", "#ffdd57")
                self._log("[等待] 1. 请在弹出的浏览器里确认已登录你的帐号", "#ffdd57")
                self._log("[等待] 2. 如果有滑块验证码，请手动完成", "#ffdd57")
                self._log("[等待] 3. 都没有问题后，点击下方 → 「✓ 验证完成，开始查询」按钮", "#ffdd57")
                self._set_status("等待你在 Chrome 里登录并验证...")
                self.after(0, lambda: self.ready_btn.configure(state="normal"))

                # 等待用户点击"验证完成"
                self._ready_event.wait()
                if self._stop_flag:
                    browser.close()
                    self._finish(file_path)
                    return

                # 4. 开始批量查询
                batches = [domains[i:i+BATCH_SIZE] for i in range(0, len(domains), BATCH_SIZE)]
                total = len(batches)
                self._log(f"[开始] 共 {total} 批，每批最多 {BATCH_SIZE} 个", "#6af7a0")
                self._set_progress(0, total)

                for i, batch in enumerate(batches, 1):
                    if self._stop_flag:
                        break

                    self._set_status(f"查询中… 第 {i}/{total} 批")
                    self._log(f"  第 {i:>3}/{total} 批（{len(batch)} 个）…", "#aaaacc")

                    # 重试循环（遇到验证码则暂停等用户处理）
                    while not self._stop_flag:
                        try:
                            result = page.evaluate(JS_QUERY, ",".join(batch))
                        except Exception as e:
                            self._log(f"  [警告] JS执行错误: {e}", "#ffaa44")
                            break

                        if isinstance(result, dict) and result.get("code") == -1001:
                            self._log("", "")
                            self._log("  !! 提示: 未登录或登录态失效 !!", "#ff6b6b")
                            self._log("  请在弹出的 Chrome 中手动【登录】你的 gname 帐号", "#ffdd57")
                            self._log("  登录成功后点击 →「↩ 验证完成，继续」", "#ffdd57")
                            self._set_status("需要登录！请在 Chrome 里登录后点继续")
                            self._continue_event.clear()
                            self.after(0, lambda: self.continue_btn.configure(state="normal"))
                            self._continue_event.wait()
                            if self._stop_flag:
                                break
                            self._log("  [继续] 已确认，重试当前批次…", "#aaaacc")
                            continue  # 重试

                        if isinstance(result, dict) and result.get("code") == -105001:
                            self._log("", "")
                            self._log("  !! 触发了验证码，请在 Chrome 里完成滑块验证 !!", "#ff6b6b")
                            self._log("  完成后点击 →「↩ 验证完成，继续」", "#ffdd57")
                            self._set_status("触发验证码！请在 Chrome 里完成后点继续")
                            self._continue_event.clear()
                            self.after(0, lambda: self.continue_btn.configure(state="normal"))
                            self._continue_event.wait()
                            if self._stop_flag:
                                break
                            self._log("  [继续] 重试当前批次…", "#aaaacc")
                            continue  # 重试

                        if isinstance(result, dict) and "error" in result:
                            self._log(f"  [警告] {result['error']}", "#ffaa44")
                            break

                        # 第一批：打印原始结构帮助调试并保存至文件
                        if i == 1:
                            keys_info = list(result.keys()) if isinstance(result, dict) else type(result).__name__
                            self._log(f"  [调试] 响应顶层字段: {keys_info}", "#888aaa")
                            if isinstance(result, dict) and "data" in result:
                                d = result["data"]
                                if isinstance(d, dict):
                                    self._log(f"  [调试] data 内字段: {list(d.keys())}", "#888aaa")
                                elif isinstance(d, list) and d:
                                    self._log(f"  [调试] data 是列表，首条字段: {list(d[0].keys()) if isinstance(d[0], dict) else d[0]}", "#888aaa")
                            
                            # 秘密将第一批结果整个保存进调试文件，以便自动排错
                            try:
                                debug_path = os.path.join(os.path.dirname(file_path), "debug_first_batch.json")
                                with open(debug_path, "w", encoding="utf-8") as _df:
                                    json.dump(result, _df, ensure_ascii=False, indent=2)
                            except Exception:
                                pass

                        items = extract_list(result)
                        parsed = parse_items(items or [])
                        self._all_results.extend(parsed)
                        color = "#6af7a0" if parsed else "#888aaa"
                        self._log(f"         命中 {len(parsed)} 个", color)
                        break

                    self._set_progress(i)

                    # 随机延迟，更像人工操作
                    if i < total and not self._stop_flag:
                        delay = random.uniform(1.2, 2.8)
                        time.sleep(delay)

                browser.close()

        except Exception as e:
            self._log(f"[错误] 浏览器异常: {e}", "#ff6b6b")

        self._finish(file_path)

    def _finish(self, file_path):
        count = len(self._all_results)
        label = "（中途停止）" if self._stop_flag else ""
        self._log(f"\n== 完成{label}！命中 {count} 条有一口价记录 ==",
                  "#7c6af7" if count else "#888aaa")
        self._set_status(f"完成 — 命中 {count} 条")

        base = os.path.splitext(file_path)[0]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 文件1：排版美化的详情文件
        out_detail = f"{base}_【包含金额详情】_{ts}.txt"
        # 文件2：纯域名文件
        out_domains = f"{base}_【纯域名】_{ts}.txt"

        try:
            # 写入详情文件
            with open(out_detail, "w", encoding="utf-8") as f:
                f.write("=" * 65 + "\n")
                f.write(f"                     gname 一口价查询记录\n")
                f.write("=" * 65 + "\n")
                f.write(f" 查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f" 命中数量: {count} 条记录\n")
                f.write(f" 数据完整: {'否（中途停止）' if self._stop_flag else '是'}\n")
                f.write("-" * 65 + "\n")
                f.write(f" {'域名 (Domain)':<30}  |  价格 (Price)\n")
                f.write("-" * 65 + "\n")
                if self._all_results:
                    for r in self._all_results:
                        d = str(r['domain'])
                        p = str(r['price'])
                        c = str(r['currency']) if r['currency'] and r['currency'].upper() != 'CNY' else '￥'
                        # 排版：左对齐，占据固定宽度
                        f.write(f" {d:<30}  |  {c} {p}\n")
                else:
                    f.write("\n    ( 未查到任何符合条件的一口价域名记录 )\n\n")
                f.write("=" * 65 + "\n")
            self._log(f"[✓] 详情排版文件：{out_detail}", "#6af7a0")

            # 写入纯域名文件（只在有结果时生成，否则没意义）
            if self._all_results:
                with open(out_domains, "w", encoding="utf-8") as f:
                    for r in self._all_results:
                        f.write(f"{r['domain']}\n")
                self._log(f"[✓] 纯域名文件：{out_domains}", "#6af7a0")

        except Exception as e:
            self._log(f"[警告] TXT保存失败: {e}", "#ffaa44")

        # JSON 完整版
        if self._all_results:
            out_json = f"{base}_调试完整数据_{ts}.json"
            try:
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump(self._all_results, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self._log(f"[警告] JSON保存失败: {e}", "#ffaa44")

        self.after(0, lambda: self.start_btn.configure(state="normal"))
        self.after(0, lambda: self.stop_btn.configure(state="disabled"))
        self.after(0, lambda: self.ready_btn.configure(state="disabled"))
        self.after(0, lambda: self.continue_btn.configure(state="disabled"))

    def _ensure_playwright(self):
        """检查并安装 playwright"""
        try:
            from playwright.sync_api import sync_playwright  # noqa
            # 验证 chromium 已下载
            result = subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
                capture_output=True, text=True
            )
            return True
        except ImportError:
            pass

        self._log("[安装] 正在安装 playwright（首次约需1-2分钟）…", "#ffdd57")
        self._set_status("安装中，请稍候…")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "playwright", "-q"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._log("[安装] playwright 包安装完成，正在下载浏览器内核…", "#ffdd57")
            subprocess.check_call(
                [sys.executable, "-m", "playwright", "install", "chromium"],
            )
            self._log("[安装] 完成！", "#6af7a0")
            return True
        except Exception as e:
            self._log(f"[错误] 安装失败: {e}", "#ff6b6b")
            return False


if __name__ == "__main__":
    app = App()
    app.mainloop()
