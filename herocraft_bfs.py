#!/usr/bin/env python3
"""
HeroCraft 广度合成脚本
======================
自动用所有已有对象两两组合（add 操作），发现新对象重建对象库。
重复结果自动销毁换许愿值。体力不足时等待恢复。
进度保存到 bfs_progress.json，可断点续跑。

用法:
    python herocraft_bfs.py                  # 持续运行
    python herocraft_bfs.py --duration 3600  # 运行 1 小时
    python herocraft_bfs.py --once           # 只跑一轮
"""

import json
import os
import sys
import time
import itertools
import argparse
from datetime import datetime
import urllib.request
import urllib.error

# ==================== 配置 ====================
BASE_URL = "http://toogle.club:36024"
LOGIN_NAME = os.environ.get("HC_LOGIN", "纪澪月")
PASSWORD = os.environ.get("HC_PASSWORD", "ghd970516")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRESS_FILE = os.path.join(SCRIPT_DIR, "bfs_progress.json")
DISCOVER_LOG = os.path.join(SCRIPT_DIR, "bfs_discovered.txt")

# ==================== HTTP ====================
_cookie = ""

def api(method, path, body=None):
    global _cookie
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept-Language", "zh-CN,zh;q=0.9")
    if _cookie:
        req.add_header("Cookie", _cookie)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        for c in (resp.headers.get_all("Set-Cookie") or []):
            if "hc_session" in c:
                _cookie = c.split(";")[0]
                break
        return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


# ==================== 工具函数 ====================

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def login():
    status, body = api("POST", "/api/auth/login",
                       {"login": LOGIN_NAME, "password": PASSWORD})
    if status != 200:
        log(f"登录失败: {status} {body[:100]}")
        sys.exit(1)
    user = json.loads(body).get("user", {})
    log(f"登录成功 | {user.get('nickname')} | "
        f"许愿值:{user.get('wish_points')} | 钻石:{user.get('diamonds')}")
    return user


def get_user():
    status, body = api("GET", "/api/auth/me")
    if status == 200:
        return json.loads(body).get("user", {})
    return {}


def get_objects():
    """返回 {id: obj_dict}"""
    status, body = api("GET", "/api/objects/mine")
    if status == 200:
        items = json.loads(body).get("items", [])
        return {obj["id"]: obj for obj in items}
    return {}


def get_failed_pairs():
    """从 API 获取已知失败的 add 组合"""
    status, body = api("GET", "/api/craft/failures")
    if status != 200:
        return set()
    data = json.loads(body)
    items = data.get("items", data.get("data", []))
    failed = set()
    for item in items:
        if item.get("operation") == "add":
            a = item.get("ingredient_a", {}).get("id")
            b = item.get("ingredient_b", {}).get("id")
            if a and b:
                failed.add(tuple(sorted([a, b])))
    return failed


def do_craft(a, b):
    """
    合成两个对象。
    返回: (status, result_obj, error_msg)
      status=True  → 成功，result_obj 有值
      status=False → LLM 判定无意义，error_msg 有值
      status=None  → 体力不足
    """
    status, body = api("POST", "/api/craft",
                       {"ingredient_ids": [a, b], "operation": "add"})
    if status == 400:
        try:
            detail = json.loads(body).get("detail", body[:100])
        except Exception:
            detail = body[:100]
        if "体力" in detail:
            return None, None, "体力不足"
        return False, None, detail
    try:
        data = json.loads(body)
        if data.get("success"):
            return True, data["result"], None
        return False, None, data.get("failure_reason", "未知原因")
    except Exception:
        return False, None, f"HTTP {status}: {body[:100]}"


def do_destroy(obj_id):
    """销毁对象，返回最新许愿值"""
    status, body = api("DELETE", f"/api/objects/{obj_id}")
    if status == 200:
        try:
            data = json.loads(body)
            if data.get("ok"):
                return data.get("wish_points")
        except Exception:
            pass
    return None


def append_discover(emoji, name, from_a, from_b):
    """追加发现记录到日志文件"""
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {emoji}{name} = {from_a} + {from_b}\n"
    try:
        with open(DISCOVER_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ==================== 进度管理 ====================

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                tried = set(tuple(p) for p in data.get("tried_pairs", []))
                discovered = data.get("discovered", {})
                return tried, discovered
        except Exception:
            pass
    return set(), {}


def save_progress(tried_pairs, discovered):
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "tried_pairs": [list(p) for p in tried_pairs],
                "discovered": discovered,
                "discovered_count": len(discovered),
                "updated_at": datetime.now().isoformat()
            }, f, ensure_ascii=False)
    except Exception:
        pass


# ==================== 主逻辑 ====================

def main():
    parser = argparse.ArgumentParser(description="HeroCraft 广度合成")
    parser.add_argument("--duration", type=int, default=0,
                        help="最长运行秒数 (0=无限)")
    parser.add_argument("--once", action="store_true",
                        help="只跑一轮")
    args = parser.parse_args()

    start_time = time.time()

    # 登录
    login()

    # 加载进度
    tried_pairs, discovered = load_progress()

    # 获取已知失败组合
    api_failures = get_failed_pairs()
    skip_pairs = tried_pairs | api_failures

    objects = get_objects()
    log(f"当前对象: {len(objects)} 个 | 已尝试: {len(tried_pairs)} | "
        f"已知失败: {len(api_failures)} | 已发现: {len(discovered)}")

    round_num = 0
    total_new = 0
    total_dup = 0
    total_fail = 0
    last_wp = 0

    while True:
        # 超时检查
        if args.duration > 0 and int(time.time() - start_time) >= args.duration:
            log(f"达到最长运行时间 {args.duration} 秒，停止。")
            break

        round_num += 1
        objects = get_objects()
        obj_ids = sorted(objects.keys())

        # 生成所有未尝试的组合
        all_pairs = list(itertools.combinations(obj_ids, 2))
        pending = [p for p in all_pairs if p not in skip_pairs]

        log(f"\n{'='*55}")
        log(f"第 {round_num} 轮 | 对象:{len(obj_ids)} | "
            f"待尝试:{len(pending)} | 已尝试:{len(skip_pairs)}")
        log(f"{'='*55}")

        if not pending:
            log("所有现有组合都已尝试完毕！")
            if args.once:
                break
            user = get_user()
            energy = user.get("stamina", 0) + user.get("stamina_reserve", 0)
            if energy == 0:
                log("体力不足，等待 5 分钟后重试...")
                time.sleep(300)
            else:
                log("有体力但无新组合。等待 60 秒...")
                time.sleep(60)
            continue

        # 逐个尝试
        round_new = 0
        round_dup = 0
        round_fail = 0
        energy_out = False

        for i, (a, b) in enumerate(pending):
            # 超时检查
            if args.duration > 0 and int(time.time() - start_time) >= args.duration:
                log(f"达到最长运行时间，停止。")
                break

            pair = tuple(sorted([a, b]))

            # 尝试合成
            success, result, error = do_craft(a, b)

            if success is None:
                # 体力不足
                log(f"  [{i+1}/{len(pending)}] 体力不足，停止本轮，等待恢复...")
                energy_out = True
                break

            elif success:
                tried_pairs.add(pair)
                skip_pairs.add(pair)

                if result["id"] in objects:
                    # 重复 → 销毁换许愿值
                    wp = do_destroy(result["id"])
                    round_dup += 1
                    total_dup += 1
                    if wp:
                        last_wp = wp
                    name_a = objects[a].get("name", a)
                    name_b = objects[b].get("name", b)
                    emoji_a = objects[a].get("emoji", "")
                    emoji_b = objects[b].get("emoji", "")
                    log(f"  [{i+1}/{len(pending)}] {emoji_a}{name_a}+{emoji_b}{name_b} "
                        f"= {result['emoji']}{result['name']} (重复->销毁 | WP:{wp})")
                else:
                    # 新发现
                    round_new += 1
                    total_new += 1
                    objects[result["id"]] = result
                    name_a = objects[a].get("name", a)
                    name_b = objects[b].get("name", b)
                    emoji_a = objects[a].get("emoji", "")
                    emoji_b = objects[b].get("emoji", "")
                    discovered[str(result["id"])] = {
                        "name": f"{result['emoji']}{result['name']}",
                        "from": f"{name_a}+{name_b}"
                    }
                    append_discover(result.get("emoji", ""), result["name"],
                                    name_a, name_b)
                    log(f"  [{i+1}/{len(pending)}] {emoji_a}{name_a}+{emoji_b}{name_b} "
                        f"= {result['emoji']}{result['name']} ** 新发现! **")

            else:
                tried_pairs.add(pair)
                skip_pairs.add(pair)
                round_fail += 1
                total_fail += 1
                if "体力" not in str(error):
                    name_a = objects[a].get("name", a)
                    name_b = objects[b].get("name", b)
                    log(f"  [{i+1}/{len(pending)}] {name_a}+{name_b} "
                        f"-> 失败: {str(error)[:50]}")

            # 每 20 次保存进度 + 打印状态
            if (i + 1) % 20 == 0:
                save_progress(tried_pairs, discovered)
                user = get_user()
                energy = user.get("stamina", 0) + user.get("stamina_reserve", 0)
                log(f"  -- 进度:{i+1}/{len(pending)} | "
                    f"体力:{user.get('stamina',0)}+{user.get('stamina_reserve',0)}"
                    f"={energy} | 本轮 新:{round_new} 重复:{round_dup} 失败:{round_fail}")

            # 短暂间隔，避免请求过快
            time.sleep(0.3)

        # 保存进度
        save_progress(tried_pairs, discovered)

        log(f"\n第 {round_num} 轮完成: 新发现 {round_new} | "
            f"重复销毁 {round_dup} | 失败 {round_fail}")
        log(f"累计: 新发现 {total_new} | 重复销毁 {total_dup} | "
            f"失败 {total_fail} | 许愿值 {last_wp}")

        if args.once:
            break

        # 体力耗尽 → 等待恢复
        if energy_out:
            user = get_user()
            stamina = user.get("stamina", 0)
            reserve = user.get("stamina_reserve", 0)
            log(f"体力 {stamina}/30 + 储备 {reserve}/60 耗尽，"
                f"等待 5 分钟恢复...")
            time.sleep(300)
        else:
            time.sleep(2)

    # 最终统计
    final_objects = get_objects()
    final_user = get_user()
    log(f"\n{'='*55}")
    log(f"完成!")
    log(f"{'='*55}")
    log(f"对象: {len(objects)} -> {len(final_objects)}")
    log(f"新发现: {total_new} | 重复销毁: {total_dup} | 失败: {total_fail}")
    log(f"许愿值: {final_user.get('wish_points', '?')} | "
        f"钻石: {final_user.get('diamonds', '?')}")
    log(f"体力: {final_user.get('stamina',0)}/30 | "
        f"储备: {final_user.get('stamina_reserve',0)}/60")
    save_progress(tried_pairs, discovered)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("用户中断，退出。")
    except Exception as e:
        log(f"异常退出: {e}")
        import traceback
        traceback.print_exc()
