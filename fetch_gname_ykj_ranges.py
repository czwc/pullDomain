#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gname.net fixed-price domain fetcher by price ranges.

Default query:
  cxfs=0
  ymhz=com,cc,net
  zcsj_2=2025
  dqsj_1=30
  jylx=nei
  pagesize=500
  price ranges: 0-20, 20.01-30, 30.01-40, ... 90.01-100

The script opens a real browser and sends requests from the gname page context.
If login or slider verification is required, finish it in the browser, then
press Enter in the terminal to retry the current request.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Callable
from urllib.parse import parse_qsl


SALES_URL = "https://www.gname.net/sales"
API_PATH = "/request/get_ykj_list"
DEFAULT_PROFILE_DIR = "gname_browser_profile"

JS_QUERY = """
async ({ endpoint, payload }) => {
    try {
        const resp = await fetch(endpoint, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01'
            },
            body: new URLSearchParams(payload)
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


def import_price_for_range(range_max: str) -> str:
    high = Decimal(str(range_max))
    if high <= Decimal("20"):
        return "80"
    steps = ((high - Decimal("20")) / Decimal("10")).to_integral_value(rounding=ROUND_CEILING)
    return str(80 + int(steps) * 20)


def parse_decimal(value: str, name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{name} must be a number: {value}") from exc


def parse_date_value(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD format: {value}") from exc


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


def build_price_ranges(args: argparse.Namespace) -> list[tuple[Decimal, Decimal]]:
    if args.ranges:
        return parse_range_spec(args.ranges)

    start = args.price_start
    first_end = args.first_end
    final_end = args.price_end
    step = args.step
    offset = args.next_offset

    if step <= 0:
        raise ValueError("--step must be greater than 0")
    if start > final_end:
        raise ValueError("--price-start cannot be greater than --price-end")
    if first_end < start:
        raise ValueError("--first-end cannot be less than --price-start")

    first_high = min(first_end, final_end)
    ranges = [(start, first_high)]
    current_high = first_end + step

    while first_high < final_end:
        current_low = current_high - step + offset
        if current_low > final_end:
            break
        ranges.append((current_low, min(current_high, final_end)))
        if current_high >= final_end:
            break
        current_high += step

    return ranges


def extract_list(data: Any) -> list[dict[str, Any]] | None:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return None

    for key in ("data", "list", "result", "rows", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for child_key in ("list", "rows", "items", "data"):
                child_value = value.get(child_key)
                if isinstance(child_value, list):
                    return [x for x in child_value if isinstance(x, dict)]
    return None


def extract_count(data: Any) -> int | None:
    def to_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.replace(",", "").strip()
            if text.isdigit():
                return int(text)
        return None

    if not isinstance(data, dict):
        return None

    for key in ("count", "total", "total_count", "records", "totalCount"):
        count = to_int(data.get(key))
        if count is not None:
            return count

    for key in ("data", "result", "rows", "items"):
        value = data.get(key)
        if isinstance(value, dict):
            for child_key in ("count", "total", "total_count", "records", "totalCount"):
                count = to_int(value.get(child_key))
                if count is not None:
                    return count

    return None


def parse_items(
    items: list[dict[str, Any]],
    price_min: Decimal,
    price_max: Decimal,
    page_no: int,
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for item in items:
        domain = (
            item.get("ym")
            or item.get("domain")
            or item.get("dname")
            or item.get("domain_name")
            or item.get("name")
            or ""
        )
        price = (
            item.get("qian")
            or item.get("price")
            or item.get("sell_price")
            or item.get("jg")
            or item.get("ykj_price")
            or item.get("cny_price")
            or item.get("rmb_price")
            or ""
        )
        currency = item.get("currency") or item.get("currency_type") or "CNY"
        if not domain:
            continue
        parsed.append(
            {
                "domain": str(domain),
                "price": str(price),
                "currency": str(currency),
                "range_min": money(price_min),
                "range_max": money(price_max),
                "page": page_no,
                "raw": item,
            }
        )
    return parsed


def page_signature(items: list[dict[str, Any]]) -> tuple[str, ...]:
    signature: list[str] = []
    for item in items:
        key = item.get("id") or item.get("ym") or item.get("domain") or item.get("name")
        if key is not None:
            signature.append(str(key))
    return tuple(signature)


def parse_extra_params(extra: str) -> dict[str, str]:
    if not extra:
        return {}
    return {key: value for key, value in parse_qsl(extra, keep_blank_values=True)}


def build_fbsj_params(args: argparse.Namespace) -> dict[str, str]:
    param_names = [part.strip() for part in args.fbsj_param.split(",") if part.strip()]
    if not param_names:
        raise ValueError("--fbsj-param cannot be empty")

    if args.fbsj:
        if len(param_names) != 1:
            raise ValueError("--fbsj can only be used with a single --fbsj-param name")
        return {param_names[0]: args.fbsj}

    has_manual_range = bool(args.fbsj_start or args.fbsj_end)
    if args.fbsj_days and has_manual_range:
        raise ValueError("--fbsj-days cannot be used together with --fbsj-start/--fbsj-end")

    start: date | None = None
    end: date | None = None
    if args.fbsj_days:
        if args.fbsj_days < 1:
            raise ValueError("--fbsj-days must be greater than 0")
        end = date.today()
        start = end - timedelta(days=args.fbsj_days - 1)
    elif has_manual_range:
        if not args.fbsj_start or not args.fbsj_end:
            raise ValueError("--fbsj-start and --fbsj-end must be provided together")
        start = parse_date_value(args.fbsj_start, "--fbsj-start")
        end = parse_date_value(args.fbsj_end, "--fbsj-end")

    if start is None or end is None:
        return {}
    if start > end:
        raise ValueError("fbsj start date cannot be later than end date")

    start_text = start.isoformat()
    end_text = end.isoformat()
    if len(param_names) == 1:
        return {param_names[0]: f"{start_text}{args.fbsj_separator}{end_text}"}
    if len(param_names) == 2:
        return {param_names[0]: start_text, param_names[1]: end_text}
    raise ValueError("--fbsj-param supports either one name or two comma-separated names")


def build_payload(
    args: argparse.Namespace,
    extra_params: dict[str, str],
    fbsj_params: dict[str, str],
    price_min: Decimal,
    price_max: Decimal,
    page_no: int,
) -> dict[str, str]:
    payload = {
        "cxfs": args.cxfs,
        "ymhz": args.ymhz,
        "zcsj_2": args.zcsj_2,
        "dqsj_1": args.dqsj_1,
        "jylx": args.jylx,
        "pagesize": str(args.pagesize),
    }

    for key in ("zcsj_1", "dqsj_2"):
        value = getattr(args, key)
        if value:
            payload[key] = value

    payload.update(fbsj_params)
    payload.update(extra_params)
    payload["qian_1"] = money(price_min)
    payload["qian_2"] = money(price_max)
    payload["page"] = str(page_no)
    payload["pagesize"] = str(args.pagesize)
    return {key: str(value) for key, value in payload.items() if value is not None}


def wait_for_manual_fix(code: int | None) -> None:
    if code == -1001:
        print("\n[需要登录] gname 提示未登录或登录超时。")
        print("请在弹出的浏览器里登录账号，确认页面正常后回到这里。")
    elif code == -105001:
        print("\n[需要验证] gname 触发滑块或人机验证。")
        print("请在弹出的浏览器里手动完成验证，确认页面正常后回到这里。")
    else:
        print("\n[需要处理] 当前请求没有成功。")
    input("处理完成后按 Enter 重试当前页...")


def fetch_page(page: Any, endpoint: str, payload: dict[str, str]) -> Any:
    return page.evaluate(JS_QUERY, {"endpoint": endpoint, "payload": payload})


def fetch_with_retry(
    page: Any,
    endpoint: str,
    payload: dict[str, str],
    retry_limit: int,
    retry_delay: float,
    manual_fix_handler: Callable[[int | None], None] | None = None,
) -> Any:
    attempts = 0
    while True:
        result = fetch_page(page, endpoint, payload)

        code = result.get("code") if isinstance(result, dict) else None
        if code in (-1001, -105001):
            if manual_fix_handler:
                manual_fix_handler(code)
            else:
                wait_for_manual_fix(code)
            continue

        if isinstance(result, dict) and result.get("error"):
            attempts += 1
            print(f"[警告] 请求失败: {result.get('error')}")
            if result.get("text"):
                print(f"[调试] 响应片段: {result.get('text')}")
            if attempts > retry_limit:
                return result
            print(f"[重试] {retry_delay:.1f} 秒后重试当前页...")
            time.sleep(retry_delay)
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
    fbsj_params: dict[str, str],
    price_min: Decimal,
    price_max: Decimal,
    all_rows: list[dict[str, Any]],
    log: Callable[[str], None] = print,
    manual_fix_handler: Callable[[int | None], None] | None = None,
) -> int:
    page_no = 1
    seen_pages: set[tuple[str, ...]] = set()
    returned_total = 0
    count_total: int | None = None

    while True:
        payload = build_payload(
            args,
            extra_params,
            fbsj_params,
            price_min,
            price_max,
            page_no,
        )
        result = fetch_with_retry(
            page,
            args.endpoint,
            payload,
            args.retries,
            args.retry_delay,
            manual_fix_handler,
        )

        items = extract_list(result)
        if count_total is None:
            count_total = extract_count(result)
            if count_total is not None:
                log(f"  [总数] 当前条件 count={count_total}")

        if items is None:
            preview = json.dumps(result, ensure_ascii=False)[:500]
            log(f"[警告] 无法识别响应结构，跳过当前区间。响应: {preview}")
            break

        signature = page_signature(items)
        if signature and signature in seen_pages:
            log("  [停止] 当前页内容重复，结束该区间，避免死循环。")
            break
        if signature:
            seen_pages.add(signature)

        rows = parse_items(items, price_min, price_max, page_no)
        all_rows.extend(rows)
        written_count = len(rows)

        returned_total += len(items)
        progress = f"{returned_total}"
        if count_total is not None:
            progress = f"{returned_total}/{count_total}"
        log(
            f"  page {page_no}: 返回 {len(items)} 条，"
            f"本区间已取 {progress} 条，写入 {written_count} 条，累计 {len(all_rows)} 条"
        )

        if len(items) < args.pagesize:
            break
        if args.max_pages_per_range and page_no >= args.max_pages_per_range:
            log("  [停止] 已达到 max-pages-per-range。")
            break

        page_no += 1
        time.sleep(random.uniform(args.delay_min, args.delay_max))

    return returned_total


def save_outputs(rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[str, str, str, str]:
    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{args.output_prefix}_{timestamp}"

    csv_path = os.path.join(args.output_dir, f"{prefix}.csv")
    json_path = os.path.join(args.output_dir, f"{prefix}_full.json")
    domains_path = os.path.join(args.output_dir, f"{prefix}_domains.txt")
    import_csv_path = os.path.join(args.output_dir, f"{prefix}_0612_format.csv")

    csv_fields = [
        "range_min",
        "range_max",
        "page",
        "domain",
        "price",
        "currency",
        "fbsj",
        "zcsj",
        "dqsj",
        "ym_nian",
        "ggsl",
        "bdsl",
        "da",
        "pa",
        "jj",
    ]

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            raw = row.get("raw") or {}
            writer.writerow(
                {
                    "range_min": row.get("range_min", ""),
                    "range_max": row.get("range_max", ""),
                    "page": row.get("page", ""),
                    "domain": row.get("domain", ""),
                    "price": row.get("price", ""),
                    "currency": row.get("currency", ""),
                    "fbsj": raw.get("fbsj", ""),
                    "zcsj": raw.get("zcsj", ""),
                    "dqsj": raw.get("dqsj", ""),
                    "ym_nian": raw.get("ym_nian", ""),
                    "ggsl": raw.get("ggsl", ""),
                    "bdsl": raw.get("bdsl", ""),
                    "da": raw.get("da", ""),
                    "pa": raw.get("pa", ""),
                    "jj": raw.get("jj", ""),
                }
            )

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(rows, file, ensure_ascii=False, indent=2)

    with open(domains_path, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(f"{row['domain']}\n")

    import_fields = [
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
    with open(import_csv_path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=import_fields)
        writer.writeheader()
        for row in rows:
            import_price = import_price_for_range(str(row.get("range_max", "0")))
            writer.writerow(
                {
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
            )

    return csv_path, json_path, domains_path, import_csv_path


def run(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[错误] 未安装 playwright。")
        print("请先运行: pip install playwright")
        print("然后运行: python -m playwright install chromium")
        return 1

    try:
        ranges = build_price_ranges(args)
    except ValueError as exc:
        print(f"[错误] 价格区间配置有误: {exc}")
        return 1

    try:
        fbsj_params = build_fbsj_params(args)
    except ValueError as exc:
        print(f"[错误] 发布时间参数配置有误: {exc}")
        return 1

    extra_params = parse_extra_params(args.extra)
    print("[配置] 即将查询价格区间:")
    print("       " + ", ".join(f"{money(low)}-{money(high)}" for low, high in ranges))
    print(f"[配置] 后缀: {args.ymhz}")
    print(f"[配置] pagesize: {args.pagesize}")
    if fbsj_params:
        print("[配置] 发布时间: " + "&".join(f"{key}={value}" for key, value in fbsj_params.items()))
    else:
        print("[配置] 发布时间: 全部")
    print(f"[配置] 浏览器资料目录: {os.path.abspath(args.profile_dir)}")

    all_rows: list[dict[str, Any]] = []

    exit_code = 0
    try:
        with sync_playwright() as pw:
            context = launch_context(pw, args)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.sales_url, wait_until="domcontentloaded")

                print("\n[等待] 浏览器已打开 gname 页面。")
                print("请先在浏览器里登录账号，并手动完成滑块/人机验证。")
                input("确认页面可正常查询后，回到这里按 Enter 开始拉取...")

                for range_index, (price_min, price_max) in enumerate(ranges, 1):
                    print(f"\n[区间 {range_index}/{len(ranges)}] {money(price_min)} - {money(price_max)}")
                    fetch_range_pages(
                        page,
                        args,
                        extra_params,
                        fbsj_params,
                        price_min,
                        price_max,
                        all_rows,
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

    csv_path, json_path, domains_path, import_csv_path = save_outputs(all_rows, args)
    print("\n[完成] 拉取结束")
    print(f"[结果] 总计 {len(all_rows)} 条记录")
    print(f"[CSV]  {csv_path}")
    print(f"[JSON] {json_path}")
    print(f"[域名] {domains_path}")
    print(f"[0612格式CSV] {import_csv_path}")
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch gname fixed-price domains from the real browser context."
    )
    parser.add_argument("--sales-url", default=SALES_URL)
    parser.add_argument("--endpoint", default=API_PATH)
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--browser-channel", default="chrome")

    parser.add_argument("--cxfs", default="0")
    parser.add_argument("--ymhz", default="com,cc,net")
    parser.add_argument("--zcsj-1", dest="zcsj_1", default="")
    parser.add_argument("--zcsj-2", dest="zcsj_2", default="2025")
    parser.add_argument("--dqsj-1", dest="dqsj_1", default="30")
    parser.add_argument("--dqsj-2", dest="dqsj_2", default="")
    parser.add_argument("--jylx", default="nei")
    parser.add_argument(
        "--fbsj-days",
        type=int,
        default=0,
        help="Release-date window including today. 2 means yesterday through today.",
    )
    parser.add_argument("--fbsj-start", default="", help="Release-date start, YYYY-MM-DD.")
    parser.add_argument("--fbsj-end", default="", help="Release-date end, YYYY-MM-DD.")
    parser.add_argument(
        "--fbsj",
        default="",
        help='Raw fbsj value, for example "2026-07-01 ~ 2026-07-02".',
    )
    parser.add_argument(
        "--fbsj-param",
        default="fbsj",
        help="Field name for release date. Use two comma-separated names for start/end fields.",
    )
    parser.add_argument("--fbsj-separator", default=" ~ ")
    parser.add_argument("--pagesize", type=int, default=500)
    parser.add_argument(
        "--extra",
        default="",
        help="Additional query-string style params, for example: key=value&key2=value2",
    )

    parser.add_argument(
        "--ranges",
        default="",
        help='Explicit price ranges, for example: "0-20,20.01-30,30.01-40"',
    )
    parser.add_argument(
        "--price-start",
        type=lambda v: parse_decimal(v, "price-start"),
        default=Decimal("0"),
    )
    parser.add_argument(
        "--first-end",
        type=lambda v: parse_decimal(v, "first-end"),
        default=Decimal("20"),
    )
    parser.add_argument(
        "--price-end",
        type=lambda v: parse_decimal(v, "price-end"),
        default=Decimal("100"),
    )
    parser.add_argument(
        "--step",
        type=lambda v: parse_decimal(v, "step"),
        default=Decimal("10"),
    )
    parser.add_argument(
        "--next-offset",
        type=lambda v: parse_decimal(v, "next-offset"),
        default=Decimal("0.01"),
    )

    parser.add_argument("--max-pages-per-range", type=int, default=0)
    parser.add_argument("--delay-min", type=float, default=1.2)
    parser.add_argument("--delay-max", type=float, default=2.8)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=3.0)

    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--output-prefix", default="gname_ykj_ranges")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.pagesize <= 0:
        print("[错误] pagesize 必须大于 0")
        return 1
    if args.pagesize > 500:
        print("[错误] gname 接口单页最大 pagesize 是 500，请设置为 500 或更小")
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
