#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gname.net 一口价域名批量查询工具
====================================================
使用前准备（只需做一次）：
  1. 用浏览器打开 https://www.gname.net/sales
  2. 按 F12 → Network → 在页面里随便查询一次域名（完成滑块验证）
  3. 在 Network 列表里找到 get_ykj_list 请求
  4. 右键 → Copy → Copy as cURL（Windows）
  5. 从 cURL 命令中找到 -H 'cookie: xxxxx' 这段，把 xxxxx 复制出来
  6. 粘贴到下方 COOKIE 变量里，保存文件

使用方法：
  python query_gname.py <域名文件.txt> [输出文件.txt]
  或直接双击运行（自动找同目录下的 txt 文件）
====================================================
"""

import requests
import time
import sys
import os
import json
from datetime import datetime

# ============================================================
#  ★★★ 第一步：填入你的 Cookie（从浏览器 F12 复制）★★★
#  如果留空，脚本会在运行时提示你输入
# ============================================================
COOKIE = ""

# ==================== 其他配置（一般不用改）====================
BATCH_SIZE    = 50    # 每批查询数量，官方页面也是50
SLEEP_BETWEEN = 2.0   # 每批之间等待秒数（防封）
API_URL = "https://www.gname.net/request/get_ykj_list"
# ============================================================


def build_headers(cookie: str) -> dict:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.gname.net",
        "Referer": "https://www.gname.net/sales",
        "X-Requested-With": "XMLHttpRequest",
    }
    if cookie:
        h["Cookie"] = cookie
    return h


def read_domains(filepath: str):
    if not os.path.exists(filepath):
        print(f"[错误] 文件不存在: {filepath}")
        sys.exit(1)
    with open(filepath, "r", encoding="utf-8") as f:
        domains = [line.strip() for line in f if line.strip()]
    print(f"[信息] 读取到 {len(domains)} 个域名")
    return domains


def query_batch(session, batch, headers, batch_no, total):
    domain_str = ",".join(batch)
    payload = {
        "cxfs": "0",
        "gjz_cha": domain_str,
        "pagesize": str(BATCH_SIZE),
        "page": "1",
    }

    try:
        resp = session.post(API_URL, data=payload, headers=headers, timeout=25)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"\n  [警告] 第{batch_no}/{total}批 网络错误: {e}")
        return [], False
    except json.JSONDecodeError:
        print(f"\n  [警告] 第{batch_no}/{total}批 非JSON响应: {resp.text[:300]}")
        return [], False

    # 检查是否被验证码拦截
    code = data.get("code", 0)
    if code == -105001:
        print(f"\n\n{'='*60}")
        print("  !! 触发了滑块验证，Cookie 已失效或未填写 !!")
        print("  请重新按照脚本顶部说明获取新 Cookie")
        print(f"{'='*60}\n")
        return [], True   # True = 需要停止

    # 解析响应数据
    items = _extract_list(data)
    if items is None:
        print(f"\n  [调试] 未知响应结构: {json.dumps(data, ensure_ascii=False)[:400]}")
        return [], False

    results = []
    for item in items:
        domain = (
            item.get("domain") or item.get("dname")
            or item.get("domain_name") or item.get("name") or ""
        )
        price = (
            item.get("price") or item.get("sell_price")
            or item.get("ykj_price") or item.get("cny_price")
            or item.get("rmb_price") or ""
        )
        currency = item.get("currency") or item.get("currency_type") or "CNY"
        if domain:
            results.append({"domain": domain, "price": price,
                            "currency": currency, "raw": item})
    return results, False


def _extract_list(data):
    """从各种可能的响应结构里取出列表"""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    # 直接在顶层找
    for key in ("data", "list", "result", "rows", "items"):
        v = data.get(key)
        if isinstance(v, list):
            return v
        # 嵌套一层
        if isinstance(v, dict):
            for k2 in ("list", "rows", "items", "data"):
                v2 = v.get(k2)
                if isinstance(v2, list):
                    return v2
    return None


def main():
    global COOKIE

    # ---------- 确定输入文件 ----------
    if len(sys.argv) >= 2:
        input_file = sys.argv[1]
    else:
        txt_files = [
            f for f in os.listdir(".")
            if f.endswith(".txt") and not f.startswith("query_gname")
        ]
        if len(txt_files) == 1:
            input_file = txt_files[0]
            print(f"[信息] 自动检测到域名文件: {input_file}")
        else:
            print("用法: python query_gname.py <域名文件.txt> [输出文件.txt]")
            sys.exit(1)

    # ---------- 确定输出文件 ----------
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        base = os.path.splitext(input_file)[0]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"{base}_gname结果_{ts}.txt"

    # ---------- Cookie ----------
    if not COOKIE:
        print()
        print("=" * 62)
        print("  需要浏览器 Cookie（用于跳过滑块验证）")
        print("  获取方法：")
        print("  1. 浏览器打开 https://www.gname.net/sales")
        print("  2. 随便查询一次域名（完成滑块验证）")
        print("  3. F12 → Network → 找 get_ykj_list 请求")
        print("  4. Headers → Request Headers → 复制 Cookie 值")
        print("=" * 62)
        COOKIE = input("  请粘贴 Cookie 并回车: ").strip()
        if not COOKIE:
            print("[警告] 未填 Cookie，将尝试无 Cookie 请求（可能失败）")

    domains = read_domains(input_file)
    if not domains:
        print("[错误] 域名列表为空")
        sys.exit(1)

    batches = [domains[i:i + BATCH_SIZE] for i in range(0, len(domains), BATCH_SIZE)]
    total = len(batches)
    headers = build_headers(COOKIE)

    print(f"[信息] 共 {total} 批，每批最多 {BATCH_SIZE} 个")
    print(f"[信息] 结果将保存至: {output_file}")
    print("-" * 60)

    all_results = []
    session = requests.Session()

    for i, batch in enumerate(batches, 1):
        print(f"  第 {i:>3}/{total} 批（{len(batch)} 个）... ", end="", flush=True)
        results, should_stop = query_batch(session, batch, headers, i, total)
        all_results.extend(results)
        print(f"命中 {len(results)} 个")

        if should_stop:
            print("[中止] 请更新 Cookie 后重试")
            break

        if i < total:
            time.sleep(SLEEP_BETWEEN)

    print("-" * 60)
    print(f"[完成] 查询 {len(domains)} 个域名，命中 {len(all_results)} 条")

    # ---------- 写出 TXT ----------
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# gname.net 一口价查询结果\n")
        f.write(f"# 查询时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# 输入文件: {input_file}\n")
        f.write(f"# 总域名数: {len(domains)}  命中数: {len(all_results)}\n")
        f.write(f"# 格式: 域名\\t售价\\t货币单位\n")
        f.write("-" * 60 + "\n")
        for r in all_results:
            f.write(f"{r['domain']}\t{r['price']}\t{r['currency']}\n")
    print(f"[信息] TXT → {output_file}")

    # ---------- 写出 JSON（含完整原始字段）----------
    json_out = output_file.replace(".txt", "_完整.json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"[信息] JSON → {json_out}")

    if all_results:
        print("\n前5条预览：")
        for r in all_results[:5]:
            print(f"  {r['domain']}  →  {r['price']} {r['currency']}")


if __name__ == "__main__":
    main()
