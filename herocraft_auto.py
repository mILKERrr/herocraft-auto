#!/usr/bin/env python3
"""
HeroCraft 自动合成+销毁脚本
=============================
每 3 分钟自动执行一次：水(1) + 火(2) = 蒸汽(20) → 销毁蒸汽 → 获取许愿值

用法:
    python herocraft_auto.py              # 默认每 180 秒循环
    python herocraft_auto.py --interval 60  # 自定义间隔 60 秒
    python herocraft_auto.py --once        # 只跑一次（测试用）

按 Ctrl+C 停止。
"""

import argparse
import os
import sys
import time
import json
from datetime import datetime

import requests

# ==================== 配置 ====================
BASE_URL = "http://toogle.club:36024"
LOGIN_NAME = os.environ.get("HC_LOGIN", "")
PASSWORD = os.environ.get("HC_PASSWORD", "")
# 水=1, 火=2  →  合成结果=蒸汽(id=20)
INGREDIENT_IDS = [1, 2]
OPERATION = "add"  # 加法合成
DEFAULT_INTERVAL = 180  # 3 分钟


# ==================== 核心函数 ====================

def log(msg):
    """带时间戳的日志输出"""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def create_session():
    """创建 HTTP 会话（不走系统代理，直连站点）"""
    s = requests.Session()
    s.trust_env = False  # 忽略 HTTP_PROXY/HTTPS_PROXY 环境变量
    s.headers.update({
        "Content-Type": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return s


def do_login(session):
    """登录，获取 hc_session cookie"""
    resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"login": LOGIN_NAME, "password": PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    user = data.get("user", data)
    log(f"登录成功 | 用户: {user.get('nickname', LOGIN_NAME)} | "
        f"许愿值: {user.get('wish_points', '?')} | "
        f"储备: {user.get('stamina_reserve', '?')}/{user.get('stamina_reserve_max', '?')}")
    return data


def do_craft(session):
    """合成两个元素，返回结果对象 ID"""
    resp = session.post(
        f"{BASE_URL}/api/craft",
        json={"ingredient_ids": INGREDIENT_IDS, "operation": OPERATION},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        reason = data.get("failure_reason", "未知原因")
        log(f"合成失败 | 原因: {reason}")
        return None

    result = data["result"]
    log(f"合成成功 | {result['emoji']} {result['name']} (ID={result['id']})")
    return result["id"]


def do_destroy(session, object_id):
    """销毁指定对象，返回最新许愿值"""
    resp = session.delete(
        f"{BASE_URL}/api/objects/{object_id}",
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        log(f"销毁失败 | 返回: {data}")
        return None

    log(f"销毁成功 | 许愿值: {data['wish_points']} | "
        f"库存: {data['object_storage_count']}/{data['object_storage_limit']}")
    return data["wish_points"]


def run_once(session):
    """执行一轮：合成 → 销毁"""
    result_id = do_craft(session)
    if result_id is None:
        return False

    time.sleep(0.5)  # 短暂等待，避免请求过快

    wish_points = do_destroy(session, result_id)
    return wish_points is not None


# ==================== 主循环 ====================

def main():
    parser = argparse.ArgumentParser(description="HeroCraft 自动合成+销毁")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"循环间隔秒数 (默认 {DEFAULT_INTERVAL})")
    parser.add_argument("--once", action="store_true",
                        help="只执行一次，不循环")
    parser.add_argument("--duration", type=int, default=0,
                        help="最长运行秒数，到时自动停止 (默认 0=无限)")
    args = parser.parse_args()

    session = create_session()
    start_time = time.time()

    # 首次登录
    try:
        do_login(session)
    except Exception as e:
        log(f"登录失败: {e}")
        sys.exit(1)

    round_num = 0
    consecutive_errors = 0

    while True:
        # 检查是否超时
        if args.duration > 0:
            elapsed = int(time.time() - start_time)
            if elapsed >= args.duration:
                log(f"达到最长运行时间 {args.duration} 秒，停止。")
                break

        round_num += 1
        log(f"========== 第 {round_num} 轮 ==========")

        try:
            success = run_once(session)
            if success:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    log("连续失败 3 次，尝试重新登录...")
                    do_login(session)
                    consecutive_errors = 0

        except requests.exceptions.HTTPError as e:
            consecutive_errors += 1
            status = e.response.status_code if e.response is not None else "?"
            log(f"HTTP 错误 {status}: {e}")

            # 401/403 说明登录过期，重新登录
            if status in (401, 403):
                log("登录已过期，重新登录...")
                try:
                    do_login(session)
                    consecutive_errors = 0
                except Exception as re:
                    log(f"重新登录失败: {re}")

        except Exception as e:
            consecutive_errors += 1
            log(f"未知错误: {e}")

        # 只跑一次
        if args.once:
            log("单次模式，结束。")
            break

        # 计算等待时间（如果接近 duration 上限则缩短）
        remaining = args.duration - int(time.time() - start_time) if args.duration > 0 else args.interval
        wait_time = min(args.interval, remaining) if args.duration > 0 else args.interval
        if wait_time <= 0:
            log(f"达到最长运行时间 {args.duration} 秒，停止。")
            break

        log(f"等待 {wait_time} 秒后进行下一轮...")
        try:
            time.sleep(wait_time)
        except KeyboardInterrupt:
            log("用户中断，退出。")
            break

    log(f"脚本结束 | 共执行 {round_num} 轮")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("用户中断，退出。")
