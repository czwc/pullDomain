#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
juming.com（聚名网）一口价域名抓取，按价格区间分页拉取。

接口: POST https://www.juming.com/ykj/get_list
请求为表单参数（pxsj/ymhz/dqsj_1/qian_1/qian_2/psize/page/jgpx/jgpx2 ...）。
成功时返回 JSON: {"code": >=0, "html": "<列表HTML片段>"}，域名数据在 html 里。
code=-401 表示需要滑块验证：在真实浏览器里人工完成验证后继续即可。

方案与 gname 工具一致：用 Playwright 打开真实 Chrome（持久化 profile 保存登录态），
在页面上下文里发 XHR，自动携带登录 Cookie 与同源 Referer。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Callable
from urllib.parse import parse_qsl


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SALES_URL = "https://www.juming.com/ykj/"
API_PATH = "/ykj/get_list"
DEFAULT_PROFILE_DIR = os.path.join(BASE_DIR, "juming_browser_profile")

JS_QUERY = """
async ({ endpoint, payload }) => {
    try {
        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(payload)) {
            params.append(key, value == null ? '' : String(value));
        }

        const resp = await fetch(endpoint, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01'
            },
            body: params
        });

        const text = await resp.text();
        try {
            return JSON.parse(text);
        } catch (e) {
            return {
                error: 'Non-JSON response',
                status: resp.status,
                text: text.slice(0, 500)
            };
        }
    } catch (e) {
        return { error: e.toString() };
    }
}
"""


def money(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def import_price_for_row(row: dict[str, Any], args: argparse.Namespace) -> str:
    price_text = str(row.get("price", "")).replace(",", "").strip()
    price_text = re.sub(r"[^0-9.]", "", price_text)
    try:
        source_price = Decimal(price_text)
    except InvalidOperation:
        source_price = Decimal("0")

    import_price = (
        source_price / args.import_price_divisor * args.import_price_multiplier
    ).to_integral_value(rounding=ROUND_CEILING)
    if import_price < args.import_min_price:
        return money(args.import_min_price)
    return str(import_price)


def parse_decimal(value: str, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number: {value}") from exc


def parse_range_spec(spec: str) -> list[tuple[Decimal, Decimal]]:
    ranges: list[tuple[Decimal, Decimal]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(\d+(?:\.\d+)?)\s*(?:-|~|:)\s*(\d+(?:\.\d+)?)$", part)
        if not match:
            raise ValueError(f"Bad range segment: {part}")
        low = Decimal(match.group(1))
        high = Decimal(match.group(2))
        if low > high:
            raise ValueError(f"Range min is greater than max: {part}")
        ranges.append((low, high))
    if not ranges:
        raise ValueError("No valid ranges were provided")
    return ranges


# ---------------------------------------------------------------------------
# 响应 HTML 片段解析
# ---------------------------------------------------------------------------

RE_TOTAL = re.compile(r"本次共查询出\s*\[\s*<strong>([\d,]+)</strong>")
RE_ROW = re.compile(r'<tr id="ymlist_(\d+)"[^>]*>(.*?)</tr>', re.S)
RE_DOMAIN = re.compile(r'class="a_ym"[^>]*>([^<]+)<')
RE_LENGTH = re.compile(r"</a></td>\s*<td>\s*(\d+)\s*</td>")
RE_PRICE = re.compile(r'data-jg="([\d.,]+)"')
RE_DQSJ = re.compile(r'<span class="dqsj">([^<]*)</span>')
RE_DQSJ_LEFT = re.compile(r"<span class=\"dqsjText\">([^<]*)</span>")
RE_SELLER = re.compile(r'class="sellid"[^>]*>\s*<a[^>]*>\s*(\d+)')
RE_XTJJ = re.compile(r"<span class=\"xtjj\">(.*?)</span>", re.S)
RE_MRJJ = re.compile(r"<span class=\"mrjj[^\"]*\">(.*?)</span>", re.S)


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_total(html_fragment: str | None) -> int | None:
    """从列表标题里取总数：本次共查询出 [ 1096311 ] 条记录。"""
    if not html_fragment:
        return None
    match = RE_TOTAL.search(html_fragment)
    if match:
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def parse_rows(
    html_fragment: str | None,
    price_min: Decimal,
    price_max: Decimal,
    page_no: int,
) -> tuple[list[dict[str, Any]], int]:
    """解析 HTML 片段里的 ymlist 行，返回 (rows, 行数)。行数含无法解析域名的行。"""
    rows: list[dict[str, Any]] = []
    item_ids = 0
    if not html_fragment:
        return rows, item_ids

    for match in RE_ROW.finditer(html_fragment):
        item_id = match.group(1)
        block = match.group(2)
        item_ids += 1

        domain_match = RE_DOMAIN.search(block)
        if not domain_match:
            continue
        domain = domain_match.group(1).strip()
        if not domain:
            continue

        price_match = RE_PRICE.search(block)
        price = (price_match.group(1) if price_match else "").strip()
        length_match = RE_LENGTH.search(block)
        if length_match:
            length = length_match.group(1)
        else:
            length = str(len(domain.rsplit(".", 1)[0]))
        dqsj_match = RE_DQSJ.search(block)
        left_match = RE_DQSJ_LEFT.search(block)
        seller_match = RE_SELLER.search(block)
        xtjj_match = RE_XTJJ.search(block)
        mrjj_match = RE_MRJJ.search(block)

        rows.append(
            {
                "id": item_id,
                "domain": domain,
                "length": length,
                "price": price,
                "seller_id": seller_match.group(1) if seller_match else "",
                "dqsj": dqsj_match.group(1).strip() if dqsj_match else "",
                "dqsj_left": left_match.group(1).strip() if left_match else "",
                "intro": strip_tags(xtjj_match.group(1)) if xtjj_match else "",
                "daily_intro": strip_tags(mrjj_match.group(1)) if mrjj_match else "",
                "range_min": money(price_min),
                "range_max": money(price_max),
                "page": page_no,
                "raw": block,
            }
        )
    return rows, item_ids


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "range_min",
    "range_max",
    "page",
    "id",
    "domain",
    "length",
    "price",
    "seller_id",
    "dqsj",
    "dqsj_left",
    "intro",
    "daily_intro",
]

IMPORT_FIELDS = [
    "Domain",
    "Buy Now Price",
    "Floor Price",
    "Min Offer",
    "Lease to Own",
    "Max Lease Period",
    "Sale Lander",
    "Show Buy Now Option",
    "Show Lease to Own Option",
    "Show Make Offer Option",
    "Hidden",
]


def csv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in CSV_FIELDS}


def import_csv_row(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    import_price = import_price_for_row(row, args)
    return {
        "Domain": row.get("domain", ""),
        "Buy Now Price": import_price,
        "Floor Price": "",
        "Min Offer": import_price,
        "Lease to Own": "N",
        "Max Lease Period": "",
        "Sale Lander": "",
        "Show Buy Now Option": "",
        "Show Lease to Own Option": "",
        "Show Make Offer Option": "",
        "Hidden": "N",
    }


def output_day_dir(output_dir: str) -> str:
    return os.path.join(output_dir, datetime.now().strftime("%Y%m%d"))


class IncrementalOutputWriter:
    """边抓边写，程序中断也不丢已抓数据。"""

    def __init__(self, args: argparse.Namespace) -> None:
        output_dir = output_day_dir(args.output_dir)
        os.makedirs(output_dir, exist_ok=True)
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        import_csv_name = now.strftime("%m%d%H%M.csv")
        prefix = f"{args.output_prefix}_{timestamp}"

        self.args = args
        self.write_csv = bool(args.export_csv)
        self.write_json = bool(args.export_json)
        self.write_import_csv = bool(args.export_import_csv)
        self.csv_path = os.path.join(output_dir, f"{prefix}.csv") if self.write_csv else ""
        self.json_path = os.path.join(output_dir, f"{prefix}_full.json") if self.write_json else ""
        self.jsonl_path = os.path.join(output_dir, f"{prefix}_full.jsonl") if self.write_json else ""
        self.import_csv_path = os.path.join(output_dir, import_csv_name) if self.write_import_csv else ""

        self._csv_file = open(self.csv_path, "w", encoding="utf-8-sig", newline="") if self.write_csv else None
        self._jsonl_file = open(self.jsonl_path, "w", encoding="utf-8") if self.write_json else None
        self._import_csv_file = open(self.import_csv_path, "w", encoding="utf-8", newline="") if self.write_import_csv else None

        self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=CSV_FIELDS) if self._csv_file else None
        if self._csv_writer:
            self._csv_writer.writeheader()
        self._import_csv_writer = (
            csv.DictWriter(self._import_csv_file, fieldnames=IMPORT_FIELDS) if self._import_csv_file else None
        )
        if self._import_csv_writer:
            self._import_csv_writer.writeheader()
        self.count = 0
        self._closed = False

    def append_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        for row in rows:
            if self._csv_writer:
                self._csv_writer.writerow(csv_row(row))
            if self._jsonl_file:
                self._jsonl_file.write(json.dumps(row, ensure_ascii=False) + "\n")
            if self._import_csv_writer:
                self._import_csv_writer.writerow(import_csv_row(row, self.args))
            self.count += 1
        self.flush()

    def flush(self) -> None:
        if self._csv_file:
            self._csv_file.flush()
        if self._jsonl_file:
            self._jsonl_file.flush()
        if self._import_csv_file:
            self._import_csv_file.flush()

    def close(self) -> None:
        if self._closed:
            return
        if self._csv_file:
            self._csv_file.close()
        if self._jsonl_file:
            self._jsonl_file.close()
        if self._import_csv_file:
            self._import_csv_file.close()
        self._closed = True

    def finalize(self) -> dict[str, str]:
        self.close()
        if self.write_json and self.jsonl_path:
            with open(self.jsonl_path, "r", encoding="utf-8") as source:
                rows = [json.loads(line) for line in source if line.strip()]
            with open(self.json_path, "w", encoding="utf-8") as target:
                json.dump(rows, target, ensure_ascii=False, indent=2)
        return {
            "csv": self.csv_path,
            "json": self.json_path,
            "jsonl": self.jsonl_path,
            "import_csv": self.import_csv_path,
        }


# ---------------------------------------------------------------------------
# 请求构建与重试
# ---------------------------------------------------------------------------

def parse_extra_params(extra: str) -> dict[str, str]:
    if not extra:
        return {}
    return {key: value for key, value in parse_qsl(extra, keep_blank_values=True)}


def build_payload(
    args: argparse.Namespace,
    extra_params: dict[str, str],
    price_min: Decimal,
    price_max: Decimal,
    page_no: int,
) -> dict[str, str]:
    jgpx = str(args.jgpx or "").strip()
    jgpx2 = str(args.jgpx2 or "").strip()
    # 复刻官网前端行为：jgpx 为奇数且 jgpx2=1（升序）时自动 +1
    if jgpx.isdigit() and int(jgpx) > 0 and int(jgpx) % 2 == 1 and jgpx2 == "1":
        jgpx = str(int(jgpx) + 1)

    payload = {
        "pxsj": str(args.pxsj or "").strip(),
        "ymbhfs": "0",
        "ymhz": str(args.ymhz or "").strip(),
        "dqsj_1": str(args.dqsj_1 or "").strip(),
        "qian_1": money(price_min),
        "qian_2": money(price_max),
        "psize": str(args.psize),
        "page": str(page_no),
        "jgpx": jgpx,
        "jgpx2": jgpx2,
    }
    payload.update(extra_params)
    return {key: str(value) for key, value in payload.items() if value != ""}


def fetch_page(page: Any, endpoint: str, payload: dict[str, str]) -> Any:
    return page.evaluate(JS_QUERY, {"endpoint": endpoint, "payload": payload})


def wait_for_manual_fix(code: int | None, msg: str) -> None:
    if code == -401:
        print("\n[需要验证] 聚名网触发滑块/人机验证。")
        print("请在弹出的浏览器里手动完成验证，确认页面正常后回到这里。")
    elif "登录" in msg:
        print("\n[需要登录] 聚名网提示未登录或登录超时。")
        print("请在弹出的浏览器里登录账号，确认页面正常后回到这里。")
    else:
        print(f"\n[需要处理] 当前请求没有成功（code={code} msg={msg}）。")
    input("处理完成后按 Enter 重试当前页...")


def fetch_with_retry(
    page: Any,
    endpoint: str,
    payload: dict[str, str],
    retry_limit: int,
    retry_delay: float,
    manual_fix_handler: Callable[[int | None, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Any:
    attempts = 0
    while True:
        if should_stop and should_stop():
            raise KeyboardInterrupt
        try:
            result = fetch_page(page, endpoint, payload)
        except Exception as exc:
            attempts += 1
            message = str(exc)
            print(f"[警告] 请求执行失败: {message}")
            if attempts > retry_limit:
                return {"error": message}
            if "Execution context was destroyed" in message or "navigation" in message.lower():
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
            print(f"[重试] {retry_delay:.1f} 秒后重试当前页...")
            if should_stop and should_stop():
                raise KeyboardInterrupt
            time.sleep(retry_delay)
            continue

        code = None
        msg = ""
        if isinstance(result, dict):
            raw_code = result.get("code")
            try:
                code = int(raw_code)
            except (TypeError, ValueError):
                code = None
            msg = str(result.get("msg", "") or "")

        if code == -401 or (code is not None and code < 0 and "登录" in msg):
            if manual_fix_handler:
                manual_fix_handler(code, msg)
            else:
                wait_for_manual_fix(code, msg)
            continue

        is_rate_limited = (
            code == -1
            or "频繁" in msg
            or "稍后再试" in msg
            or "太快" in msg
        )

        if isinstance(result, dict) and (result.get("error") or (code is not None and code < 0)):
            attempts += 1
            message = result.get("error") or msg or "未知错误"
            print(f"[警告] 请求失败: {message}")
            if result.get("text"):
                print(f"[调试] 响应片段: {result.get('text')}")
            if attempts > retry_limit:
                return result
            if is_rate_limited:
                wait = retry_delay * (2 ** attempts)
                print(f"[限流] 等待 {wait:.1f} 秒后重试当前页...")
            else:
                wait = retry_delay
                print(f"[重试] {wait:.1f} 秒后重试当前页...")
            if should_stop and should_stop():
                raise KeyboardInterrupt
            time.sleep(wait)
            continue

        return result


def launch_context(pw: Any, args: argparse.Namespace) -> Any:
    launch_args = ["--start-maximized"]
    kwargs = {
        "headless": False,
        "args": launch_args,
        "viewport": None,
    }

    profile_dir = os.path.abspath(args.profile_dir)
    os.makedirs(profile_dir, exist_ok=True)

    if args.browser_channel:
        try:
            return pw.chromium.launch_persistent_context(
                profile_dir,
                channel=args.browser_channel,
                **kwargs,
            )
        except Exception as exc:
            print(f"[提示] 无法使用 {args.browser_channel}: {exc}")
            print("[提示] 改用 Playwright Chromium。")

    return pw.chromium.launch_persistent_context(profile_dir, **kwargs)


def fetch_range_pages(
    page: Any,
    args: argparse.Namespace,
    extra_params: dict[str, str],
    price_min: Decimal,
    price_max: Decimal,
    all_rows: list[dict[str, Any]],
    log: Callable[[str], str | None] = print,
    manual_fix_handler: Callable[[int | None, str], None] | None = None,
    output_writer: IncrementalOutputWriter | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    page_no = 1
    seen_pages: set[tuple[str, ...]] = set()
    returned_total = 0
    count_total: int | None = None

    while True:
        if should_stop and should_stop():
            raise KeyboardInterrupt
        payload = build_payload(args, extra_params, price_min, price_max, page_no)
        result = fetch_with_retry(
            page,
            args.endpoint,
            payload,
            args.retries,
            args.retry_delay,
            manual_fix_handler,
            should_stop,
        )

        if should_stop and should_stop():
            raise KeyboardInterrupt

        html_fragment = result.get("html") if isinstance(result, dict) else None
        if count_total is None:
            count_total = extract_total(html_fragment)
            if count_total is not None:
                log(f"  [总数] 当前条件 count={count_total}")

        rows, item_count = parse_rows(html_fragment, price_min, price_max, page_no)

        if item_count == 0:
            if isinstance(result, dict) and result.get("msg"):
                log(f"  [停止] 当前区间没有返回数据: {result.get('msg')}")
            else:
                log("  [停止] 当前区间没有更多数据。")
            break

        signature = tuple(row["id"] for row in rows)
        if signature and signature in seen_pages:
            log("  [停止] 当前页内容重复，结束该区间，避免死循环。")
            break
        if signature:
            seen_pages.add(signature)

        all_rows.extend(rows)
        if output_writer:
            output_writer.append_rows(rows)
        written_count = len(rows)

        returned_total += item_count
        progress = f"{returned_total}"
        if count_total is not None:
            progress = f"{returned_total}/{count_total}"
        log(
            f"  page {page_no}: 返回 {item_count} 条，"
            f"本区间已取 {progress} 条，写入 {written_count} 条，累计 {len(all_rows)} 条"
        )

        if item_count < args.psize:
            break

        page_no += 1
        if should_stop and should_stop():
            raise KeyboardInterrupt
        time.sleep(random.uniform(args.delay_min, args.delay_max))

    return returned_total


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch juming.com fixed-price (ykj) domains from the real browser context."
    )
    parser.add_argument("--sales-url", default=SALES_URL)
    parser.add_argument("--endpoint", default=API_PATH)
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--browser-channel", default="chrome")

    parser.add_argument("--pxsj", default="1")
    parser.add_argument("--ymhz", default=".com")
    parser.add_argument("--dqsj-1", dest="dqsj_1", default="30")
    parser.add_argument("--psize", type=int, default=500)
    parser.add_argument("--jgpx", default="38")
    parser.add_argument("--jgpx2", default="1")
    parser.add_argument(
        "--extra",
        default="",
        help="Additional query-string style params, for example: key=value&key2=value2",
    )

    parser.add_argument(
        "--ranges",
        default="0-100",
        help='Price ranges (qian_1-qian_2), for example: "0-20,20.01-30,30.01-100"',
    )

    parser.add_argument("--delay-min", type=float, default=2.0)
    parser.add_argument("--delay-max", type=float, default=4.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=5.0)

    parser.add_argument("--output-dir", default=BASE_DIR)
    parser.add_argument("--output-prefix", default="juming_ykj_ranges")
    parser.add_argument("--import-price-divisor", type=lambda v: parse_decimal(v, "import-price-divisor"), default=Decimal("0.6"))
    parser.add_argument("--import-price-multiplier", type=lambda v: parse_decimal(v, "import-price-multiplier"), default=Decimal("1.4"))
    parser.add_argument("--import-min-price", type=lambda v: parse_decimal(v, "import-min-price"), default=Decimal("80"))
    parser.add_argument("--export-csv", action="store_true", default=True)
    parser.add_argument("--export-json", action="store_true", default=True)
    parser.add_argument("--export-import-csv", action="store_true", default=True)
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[错误] 未安装 playwright。")
        print("请先运行: pip install playwright")
        print("然后运行: python -m playwright install chromium")
        return 1

    try:
        ranges = parse_range_spec(args.ranges)
    except ValueError as exc:
        print(f"[错误] 价格区间配置有误: {exc}")
        return 1

    extra_params = parse_extra_params(args.extra)
    print("[配置] 即将查询价格区间:")
    print("       " + ", ".join(f"{money(low)}-{money(high)}" for low, high in ranges))
    print(f"[配置] 后缀: {args.ymhz}")
    print(f"[配置] psize: {args.psize}")
    print(f"[配置] 排序: jgpx={args.jgpx} jgpx2={args.jgpx2}")
    print(f"[配置] 浏览器资料目录: {os.path.abspath(args.profile_dir)}")

    all_rows: list[dict[str, Any]] = []
    output_writer = IncrementalOutputWriter(args)
    print(f"[保存] CSV: {output_writer.csv_path}")
    if output_writer.import_csv_path:
        print(f"[保存] 导入CSV: {output_writer.import_csv_path}")
    if output_writer.jsonl_path:
        print(f"[保存] JSONL: {output_writer.jsonl_path}")

    exit_code = 0
    try:
        with sync_playwright() as pw:
            context = launch_context(pw, args)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.sales_url, wait_until="domcontentloaded")

                print("\n[等待] 浏览器已打开聚名网页面。")
                print("请先在浏览器里登录账号，并手动完成滑块/人机验证。")
                input("确认页面可正常查询后，回到这里按 Enter 开始拉取...")

                for range_index, (price_min, price_max) in enumerate(ranges, 1):
                    print(f"\n[区间 {range_index}/{len(ranges)}] {money(price_min)} - {money(price_max)}")
                    fetch_range_pages(
                        page,
                        args,
                        extra_params,
                        price_min,
                        price_max,
                        all_rows,
                        output_writer=output_writer,
                    )

            finally:
                context.close()
    except KeyboardInterrupt:
        print("\n[中止] 用户取消，正在保存已抓取数据...")
        exit_code = 130
    except Exception as exc:
        print(f"\n[错误] 执行失败: {exc}")
        print("[提示] 正在保存已抓取数据...")
        exit_code = 1

    paths = output_writer.finalize()
    print("\n[完成] 拉取结束")
    print(f"[结果] 总计 {len(all_rows)} 条记录")
    if paths.get("csv"):
        print(f"[CSV]  {paths['csv']}")
    if paths.get("json"):
        print(f"[JSON] {paths['json']}")
    if paths.get("import_csv"):
        print(f"[导入CSV] {paths['import_csv']}")
    return exit_code


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.psize <= 0:
        print("[错误] psize 必须大于 0")
        return 1
    if args.psize > 500:
        print("[错误] 聚名网接口单页最大 psize 是 500，请设置为 500 或更小")
        return 1
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        print("[错误] delay 参数不合法")
        return 1

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n[中止] 用户取消")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
