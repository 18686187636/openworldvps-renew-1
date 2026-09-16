#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import base64
import random
import urllib.parse
import requests
import time
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

try:
    import numpy as np
    import cv2
except ImportError:
    print("❌ 缺少依赖：pip install numpy opencv-python-headless")
    sys.exit(1)


# ================= 配置区 =================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID", "")
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN", "")
ACCOUNT_NAME  = os.environ.get("ACCOUNT_NAME", "未命名账号")
SITE_BASE     = "https://openworld.eu.org"
RENEW_THRESHOLD_DAYS = 5
SCREENSHOT_DIR = os.environ.get("SCREENSHOT_DIR", ".")
# ==========================================

os.makedirs(SCREENSHOT_DIR, exist_ok=True)


STEALTH_JS = r"""
(function() {
  try { Object.defineProperty(navigator, 'webdriver', { get: () => false }); } catch(e) {}
  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => [{name:'PDF Viewer'},{name:'Chrome PDF Viewer'},{name:'Chromium PDF Viewer'}]
    });
  } catch(e) {}
  try { Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en','zh-CN'] }); } catch(e) {}
  try {
    if (!window.chrome) window.chrome = { runtime:{}, loadTimes:function(){}, csi:function(){}, app:{} };
  } catch(e) {}
  ['__selenium_unwrapped','__webdriver_evaluate','__selenium_evaluate','__driver_evaluate',
   '__fxdriver_evaluate','_Selenium_IDE_Recorder','__webdriver_script_fn','__webdriver_script_func',
   '__webdriver_script_url','__driver_script_fn','__driver_script_url','_phantom','__nightmare',
   'callPhantom','domAutomation','domAutomationController'].forEach(function(k){
    try { delete window[k]; } catch(e) {}
  });
  try { for (var k in window) if (k.indexOf('$cdc_')===0) { try{delete window[k];}catch(e){} } } catch(e) {}
  try { delete document.$cdc_asdjflasutopfhvcZLmcfl_; } catch(e) {}
})();
"""


# ================= Playwright 原生 WebSocket 状态 =================
WS_STATE = {
    "url": None,
    "meta": None,
    "frames": [],
    "last_resp": None,
    "sent": [],
    "closed": False,
}


def _reset_ws_state():
    WS_STATE["meta"] = None
    WS_STATE["frames"] = []
    WS_STATE["last_resp"] = None
    WS_STATE["sent"] = []
    WS_STATE["closed"] = False


def _install_ws_hook(page):
    def on_ws(ws):
        WS_STATE["url"] = ws.url
        print(f"   🔌 WebSocket: {ws.url}")

        def on_sent(payload):
            try:
                if isinstance(payload, (bytes, bytearray)):
                    WS_STATE["sent"].append(f"[bin:{len(payload)}]")
                else:
                    s = str(payload)
                    WS_STATE["sent"].append(s[:200])
                    print(f"   ➡️ 发送: {s[:140]}")
            except Exception:
                pass

        def on_recv(payload):
            try:
                if isinstance(payload, (bytes, bytearray)):
                    WS_STATE["frames"].append(bytes(payload))
                else:
                    s = str(payload)
                    WS_STATE["last_resp"] = s
                    print(f"   ⬅️ 收到: {s[:180]}")
                    try:
                        m = json.loads(s)
                        if isinstance(m, dict) and m.get("id") and m.get("nf"):
                            # 新挑战，重置 frames
                            WS_STATE["meta"] = m
                            WS_STATE["frames"] = []
                    except Exception:
                        pass
            except Exception:
                pass

        def on_close():
            WS_STATE["closed"] = True
            print("   🔒 WebSocket 关闭")

        ws.on("framesent", on_sent)
        ws.on("framereceived", on_recv)
        ws.on("close", on_close)

    page.on("websocket", on_ws)


# ================= 通用工具 =================

def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置")
        return
    full = f"👤 账号: {ACCOUNT_NAME}\n{message}"
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT_ID, "text": full}, timeout=10)
        if r.status_code == 200:
            print("✅ Telegram 已发送")
        else:
            print(f"❌ Telegram 失败: {r.status_code}")
    except Exception as e:
        print(f"❌ Telegram 异常: {e}")


def save_screenshot(page, name: str):
    try:
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"), full_page=False)
        print(f"   📸 截图: {name}.png")
    except Exception as e:
        print(f"   ⚠️ 截图失败: {e}")


def dump_page_debug(page, name: str):
    save_screenshot(page, name)
    try:
        with open(os.path.join(SCREENSHOT_DIR, f"{name}.html"), "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception:
        pass


def wait_for_cloudflare(page, timeout=15):
    inds = ["verify you are human", "just a moment", "checking your browser",
            "cf-browser-verification", "challenge-platform"]
    start = time.time()
    while time.time() - start < timeout:
        try:
            if not any(i in page.content().lower() for i in inds):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# ================= Discord 登录（不变） =================

def login_with_discord_token(page, dc_token: str) -> bool:
    print("=" * 50)
    print("🔑 Discord OAuth 登录")
    print("=" * 50)

    try:
        page.goto(SITE_BASE, wait_until="domcontentloaded", timeout=30000)
        wait_for_cloudflare(page)
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"   ⚠️ 首页: {e}")

    try:
        page.goto(f"{SITE_BASE}/login", wait_until="domcontentloaded", timeout=30000)
        wait_for_cloudflare(page)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"   ⚠️ 登录页: {e}")

    def _click(selectors, desc=""):
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    print(f"   🔘 {desc}: {sel}")
                    return True
            except Exception:
                continue
        return False

    if "discord.com" not in page.url:
        clicked = _click(["#clerk-signin", "button[id='clerk-signin']",
                          "button:has-text('Sign in with Clerk')",
                          "[data-clerk-component] button"], "Clerk 入口")
        if clicked:
            page.wait_for_timeout(4000)
            discord_sels = [
                "button:has-text('Continue with Discord')",
                "button.cl-socialButtonsBlockButton",
                "button[data-localization-key='socialButtonsBlockButton']",
                "button:has-text('Discord')",
                "a:has-text('Continue with Discord')",
            ]
            _click(discord_sels, "Clerk 内 Discord")
            try:
                page.wait_for_url(re.compile(r"discord\.com"), timeout=20000,
                                  wait_until="domcontentloaded")
                print("   ✅ 已跳到 Discord")
            except Exception:
                print("   ⚠️ 未自动跳转")

        if "discord.com" not in page.url and not clicked:
            _click(["button:has-text('Sign in with Discord')",
                    "a:has-text('Sign in with Discord')",
                    "button:has-text('Continue with Discord')",
                    "button:has-text('Discord')",
                    "a:has-text('Discord')"], "通用 Discord 入口")

    if "discord.com" not in page.url:
        for _ in range(15):
            page.wait_for_timeout(1000)
            if "discord.com" in page.url:
                break
        if "discord.com" not in page.url:
            print(f"   ❌ 无法跳转 Discord，URL: {page.url}")
            save_screenshot(page, "login_failed_no_discord")
            return False

    oauth_url = page.url
    if "discord.com/login" in oauth_url and "redirect_to=" in oauth_url:
        p = urllib.parse.urlparse(oauth_url)
        q = urllib.parse.parse_qs(p.query)
        rt = q.get("redirect_to", [""])[0]
        if rt:
            oauth_url = ("https://discord.com" + rt) if rt.startswith("/") else rt

    p = urllib.parse.urlparse(oauth_url)
    q = urllib.parse.parse_qs(p.query)
    client_id = q.get("client_id", [""])[0]
    redirect_uri = q.get("redirect_uri", [""])[0]
    scope = q.get("scope", ["identify email"])[0]
    state = q.get("state", [""])[0]
    response_type = q.get("response_type", ["code"])[0]

    print(f"   client_id={client_id[:20]}... redirect_uri={redirect_uri[:40]}...")
    if not client_id or not redirect_uri:
        print("   ❌ OAuth 参数解析失败")
        save_screenshot(page, "login_failed_parse")
        return False

    api_p = urllib.parse.urlencode({
        "client_id": client_id, "response_type": response_type,
        "redirect_uri": redirect_uri, "scope": scope, "state": state,
    })
    try:
        r = requests.post(
            f"https://discord.com/api/v9/oauth2/authorize?{api_p}",
            headers={
                "accept": "*/*",
                "authorization": dc_token.strip(),
                "content-type": "application/json",
                "origin": "https://discord.com",
                "referer": f"https://discord.com/oauth2/authorize?{api_p}",
                "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/130.0.0.0 Safari/537.36"),
            },
            json={"permissions": "0", "authorize": True, "integration_type": 0},
            timeout=20,
        )
        print(f"   API: {r.status_code}")
        if r.status_code in (401, 403):
            print("   ❌ Discord Token 失效")
            return False
        if r.status_code != 200:
            print(f"   ❌ {r.text[:200]}")
            return False
        location = r.json().get("location", "")
    except Exception as e:
        print(f"   ❌ API 异常: {e}")
        return False

    if not location:
        print("   ❌ 无 location")
        return False

    try:
        page.goto(location, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(5000)
    wait_for_cloudflare(page)

    final_url = page.url
    if "/login" in final_url and "discord" not in final_url:
        page.wait_for_timeout(5000)
        if "/login" in page.url:
            print(f"   ❌ 登录失败: {page.url}")
            save_screenshot(page, "login_callback_stuck")
            return False

    print(f"   ✅ 登录成功: {page.url}")
    save_screenshot(page, "login_success")
    return True


# ================= 拼图 / key 滑块求解 =================

def _decode(b):
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_UNCHANGED)


def _solve_puzzle(bg_bytes: bytes, piece_bytes: bytes, meta: dict) -> int:
    """
    返回 chip 左边缘在 bg 坐标系里的目标 x（即提交给服务端的 value）。
    关键：不裁剪 piece，让 max_loc 直接给出完整 piece 的左上角位置。
    """
    bg = _decode(bg_bytes)
    piece = _decode(piece_bytes)
    if bg is None or piece is None:
        raise RuntimeError("解码失败")
    if bg.ndim == 3:
        bg = bg[:, :, :3]

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    H, W = bg_gray.shape
    vmax = int(meta.get("vmax") or W)
    py = int(meta.get("py", 0))
    ph = int(meta.get("ph", piece.shape[0]))

    if piece.ndim != 3 or piece.shape[2] != 4:
        raise RuntimeError("piece 缺少 alpha 通道")

    piece_rgb = piece[:, :, :3]
    piece_gray = cv2.cvtColor(piece_rgb, cv2.COLOR_BGR2GRAY)
    mask = (piece[:, :, 3] > 100).astype(np.uint8)

    # 只在 py 附近 ROI 搜索（缺口一定在 chip top 附近）
    y0 = max(0, py - 25)
    y1 = min(H, py + ph + 25)
    bg_roi = bg_gray[y0:y1, :]

    best_val = -1.0
    best_x = 0
    for method_name, method in [
        ("CCOEFF", cv2.TM_CCOEFF_NORMED),
        ("CCORR",  cv2.TM_CCORR_NORMED),
    ]:
        try:
            res = cv2.matchTemplate(bg_roi, piece_gray, method, mask=mask)
            _, mv, _, ml = cv2.minMaxLoc(res)
            print(f"   🧪 {method_name}: max_val={mv:.3f} loc={ml}")
            if mv > best_val:
                best_val = mv
                best_x = int(ml[0])
        except Exception as e:
            print(f"   ⚠️ {method_name} 失败: {e}")

    if best_val < 0.3:
        print(f"   ⚠️ 匹配度过低 ({best_val:.3f})，可能误判")

    return max(0, min(vmax, best_x))


def _solve_odd(page, bg_bytes: bytes, meta: dict):
    """返回要点击的 item 索引。"""
    bg = _decode(bg_bytes)
    if bg is None:
        return None
    if bg.ndim == 3:
        bg = bg[:, :, :3]

    items = meta.get("items") or []
    if len(items) < 3:
        return None

    features = []
    for it in items:
        x, y, r = int(it["x"]), int(it["y"]), int(it["r"])
        x1, y1 = max(0, x - r), max(0, y - r)
        x2, y2 = min(bg.shape[1], x + r), min(bg.shape[0], y + r)
        patch = bg[y1:y2, x1:x2]
        if patch.size == 0:
            features.append(None)
            continue
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        # 用 HSV 直方图作为特征
        hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        hist = cv2.normalize(hist, hist).flatten()
        features.append(hist)

    # 找 outlier：与其它项的平均距离最大
    best_idx, best_score = -1, -1.0
    for i, fi in enumerate(features):
        if fi is None:
            continue
        total, cnt = 0.0, 0
        for j, fj in enumerate(features):
            if i == j or fj is None:
                continue
            total += float(cv2.compareHist(fi.astype(np.float32),
                                           fj.astype(np.float32),
                                           cv2.HISTCMP_BHATTACHARYYA))
            cnt += 1
        if cnt > 0:
            avg = total / cnt
            if avg > best_score:
                best_score, best_idx = avg, i
    return best_idx if best_idx >= 0 else None


def _click_box_at(page, meta: dict, img_x: float, img_y: float):
    """把 captcha 图片坐标系里的点转成页面坐标并点击。"""
    box = page.locator("#captcha_box_default").bounding_box()
    if not box:
        raise RuntimeError("captcha box 不可见")
    sx = box["width"] / float(meta.get("w", 300))
    sy = box["height"] / float(meta.get("h", 160))
    px = box["x"] + img_x * sx
    py = box["y"] + img_y * sy
    page.mouse.move(px, py)
    page.wait_for_timeout(random.randint(80, 160))
    page.mouse.click(px, py)


def _drag_slider(page, value: int, vmax: int):
    track = page.locator("#captcha_track_default")
    track.wait_for(state="visible", timeout=5000)
    box = track.bounding_box()
    if not box:
        raise RuntimeError("track 不可见")

    handle_w = 24
    usable = max(1.0, box["width"] - handle_w)
    frac = max(0.0, min(1.0, value / max(1, vmax)))
    target_x = box["x"] + handle_w / 2 + frac * usable
    y = box["y"] + box["height"] / 2
    start_x = box["x"] + handle_w / 2 + 0.03 * usable

    page.mouse.move(start_x, y)
    page.wait_for_timeout(random.randint(80, 200))
    page.mouse.down()
    page.wait_for_timeout(random.randint(60, 120))

    steps = random.randint(18, 28)
    for i in range(1, steps + 1):
        t = i / steps
        eased = 1 - (1 - t) ** 2
        x = start_x + (target_x - start_x) * eased
        yy = y + random.uniform(-1.8, 1.8)
        page.mouse.move(x, yy)
        page.wait_for_timeout(random.randint(10, 25))

    # 微过冲 + 回退，模拟人手
    overshoot = random.uniform(3, 8)
    page.mouse.move(target_x + overshoot, y + random.uniform(-2, 2))
    page.wait_for_timeout(random.randint(50, 90))
    page.mouse.move(target_x - random.uniform(1, 3), y + random.uniform(-1, 1))
    page.wait_for_timeout(random.randint(40, 80))
    page.mouse.move(target_x + random.uniform(-1, 1), y + random.uniform(-1, 1))
    page.wait_for_timeout(random.randint(30, 60))
    page.mouse.up()


# ================= 多 stage 会话处理 =================

def _wait_meta_ready(page, timeout=20):
    """等 meta + 所有帧到齐，返回 (meta, frames)。"""
    start = time.time()
    while time.time() - start < timeout:
        meta = WS_STATE["meta"]
        if meta and meta.get("id"):
            nf = int(meta.get("nf") or 1)
            if len(WS_STATE["frames"]) >= nf:
                return meta, list(WS_STATE["frames"])[:nf]
        page.wait_for_timeout(200)
    raise TimeoutError(
        f"meta 就绪超时: meta={WS_STATE['meta']}, "
        f"frames={len(WS_STATE['frames'])}, last_resp={WS_STATE['last_resp']!r}"
    )


def _wait_resp_change(page, prev_resp, timeout=20):
    """等 last_resp 变成不同于 prev_resp 的新响应。"""
    start = time.time()
    while time.time() - start < timeout:
        r = WS_STATE["last_resp"]
        if r and r != prev_resp:
            return r
        page.wait_for_timeout(200)
    return None


def _handle_one_stage(page, meta, frames, stage_label):
    """处理一关。返回 True=已提交 / False=无法处理。"""
    kind = meta.get("kind")
    print(f"   🎯 {stage_label} kind={kind} nf={meta.get('nf')} "
          f"stage={meta.get('stage')}/{meta.get('stages')}")

    if kind in ("puzzle", "key"):
        if len(frames) < 2:
            print(f"   ⚠️ nf<2")
            return False
        value = _solve_puzzle(frames[0], frames[1], meta)
        print(f"   🧩 value={value} vmax={meta.get('vmax')}")
        try:
            _drag_slider(page, value, int(meta.get("vmax") or 300))
            return True
        except Exception as e:
            print(f"   ❌ 拖动失败: {e}")
            return False

    if kind == "odd":
        if not frames:
            return False
        idx = _solve_odd(page, frames[0], meta)
        if idx is None:
            print("   ⚠️ odd 求解失败")
            return False
        items = meta.get("items") or []
        it = items[idx]
        print(f"   🎯 点击 item[{idx}] @({it['x']},{it['y']})")
        try:
            _click_box_at(page, meta, it["x"], it["y"])
            return True
        except Exception as e:
            print(f"   ❌ 点击失败: {e}")
            return False

    print(f"   ⚠️ 未支持的 kind: {kind}")
    return False


def _try_renew_once(page, attempt: int, initial_days: int):
    """
    开 renew 弹窗，处理全部 stage（mixed 模式有 5 关），全过才返回 True。
    """
    print(f"\n   {'='*40}\n   🔄 第 {attempt} 次尝试\n   {'='*40}")

    _reset_ws_state()

    # 点 Renew free
    try:
        btn = page.locator("button:has-text('Renew free')").first
        btn.wait_for(state="visible", timeout=8000)
        btn.click()
        print("   ✅ 已点击 Renew free")
    except Exception as e:
        print(f"   ❌ 未找到续期按钮: {e}")
        return None

    handled_ids = set()
    seen_next = 0
    start = time.time()
    max_total = 180  # 5 关总时长上限
    last_action = time.time()

    while time.time() - start < max_total:
        resp = WS_STATE["last_resp"]

        # ---- 终点状态 ----
        if resp and resp.startswith("ok:"):
            token = resp[3:]
            print(f"   🎉 全部通过！token 长度={len(token)}")
            # 等 Confirm Renewal 出现
            try:
                confirm = page.locator("button:has-text('Confirm Renewal')").first
                confirm.wait_for(state="visible", timeout=5000)
                confirm.click()
                print("   ✅ 已点击 Confirm Renewal")
            except Exception as e:
                print(f"   ⚠️ Confirm Renewal: {e}")

            page.wait_for_timeout(4000)
            try:
                page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            wait_for_cloudflare(page)
            page.wait_for_timeout(2000)

            try:
                text = page.locator("body").inner_text()
            except Exception:
                text = ""
            m = re.search(r"[Rr]enews?\s+in\s+(\d+)\s+days?", text)
            if m:
                new_days = int(m.group(1))
                print(f"   📊 刷新后剩余: {new_days} 天")
                if new_days > initial_days:
                    print(f"   ✅ 续期成功！{initial_days} → {new_days} 天")
                    return True
                print(f"   ❌ 未增加")
                return None
            print("   ⚠️ 无法解析剩余天数")
            save_screenshot(page, f"after_renew_a{attempt}")
            return None

        if resp in ("burned", "blocked", "rate"):
            print(f"   ❌ 服务端拒绝: {resp}")
            return None
        if resp and resp.startswith("bot:"):
            print(f"   ❌ 被识别为 bot: {resp}")
            return None

        # ---- 统计过关 ----
        if resp and resp.startswith("next:"):
            n = resp[5:]
            if n not in ("", str(seen_next)):
                seen_next += 1
                print(f"   ✓ 通过第 {seen_next} 关")

        # ---- 拿新 meta ----
        meta = WS_STATE["meta"]
        if not meta or not meta.get("id"):
            page.wait_for_timeout(300)
            if time.time() - last_action > 30:
                print("   ⚠️ 30 秒无新 meta，退出")
                return None
            continue

        meta_id = meta["id"]
        if meta_id in handled_ids:
            page.wait_for_timeout(300)
            if time.time() - last_action > 30:
                print("   ⚠️ 30 秒无新挑战，退出")
                return None
            continue

        # 等帧齐
        nf = int(meta.get("nf") or 1)
        if len(WS_STATE["frames"]) < nf:
            page.wait_for_timeout(300)
            continue

        frames = list(WS_STATE["frames"])[:nf]
        handled_ids.add(meta_id)
        last_action = time.time()

        # 保存帧供调试
        for i, fb in enumerate(frames):
            try:
                with open(os.path.join(SCREENSHOT_DIR,
                                       f"captcha_a{attempt}_{meta_id[:6]}_f{i}.png"),
                          "wb") as f:
                    f.write(fb)
            except Exception:
                pass

        stage_label = f"Stage {meta.get('stage')}/{meta.get('stages')}"
        prev_resp = WS_STATE["last_resp"]
        ok = _handle_one_stage(page, meta, frames, stage_label)
        if not ok:
            return None

        # 等服务端对本次提交的响应
        new_resp = _wait_resp_change(page, prev_resp, timeout=15)
        print(f"   📨 响应: {new_resp!r}")
        if new_resp is None:
            print("   ⚠️ 提交后无响应，可能被超时")
            return None
        if new_resp.startswith("ok:"):
            # 下一轮循环会处理
            continue
        if new_resp == "fail":
            # 服务端会重发同 stage 新挑战，继续循环
            continue
        if new_resp.startswith("next:"):
            # 过关，等服务端推下一关 meta
            continue

    print(f"   ❌ 超时 {max_total}s 未完成")
    save_screenshot(page, f"renew_timeout_a{attempt}")
    return None


def try_renew_captcha(page, initial_days: int, max_attempts=3) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            r = _try_renew_once(page, attempt, initial_days)
        except Exception as e:
            print(f"   ❌ 第 {attempt} 次异常: {e}")
            import traceback
            traceback.print_exc()
            r = None

        if r is True:
            return True

        if attempt < max_attempts:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
                page.reload(wait_until="domcontentloaded", timeout=30000)
                wait_for_cloudflare(page)
                page.wait_for_timeout(3000)
            except Exception as e:
                print(f"   ⚠️ 刷新异常: {e}")

    print(f"   ❌ {max_attempts} 次会话均失败")
    return False


# ================= VPS 列表 =================

def get_vps_urls(page) -> list:
    def extract():
        found = []
        try:
            for link in page.locator("a[href*='/vps/']").all():
                href = link.get_attribute("href") or ""
                if not href:
                    continue
                full = urllib.parse.urljoin(SITE_BASE, href)
                path = urllib.parse.urlparse(full).path.rstrip('/')
                parts = [p for p in path.split('/') if p]
                if len(parts) == 2 and parts[0] == "vps" and \
                   parts[1] not in ("list", "new", "create"):
                    if full not in found:
                        found.append(full)
        except Exception:
            pass
        return found

    print("\n🔍 识别 VPS 实例...")
    urls = extract()
    if not urls:
        try:
            page.goto(SITE_BASE, wait_until="domcontentloaded", timeout=30000)
            wait_for_cloudflare(page)
            page.wait_for_timeout(3000)
            urls = extract()
        except Exception:
            pass

    if urls:
        print(f"   ✅ 找到 {len(urls)} 个:")
        for u in urls:
            print(f"      - {u}")
    else:
        print("   ❌ 未找到 VPS")
    return urls


# ================= 主流程 =================

def main():
    print("#" * 50)
    print("   Openworld VPS 自动续期脚本 (v6 - mixed 多 stage)")
    print("#" * 50)

    if not DISCORD_TOKEN:
        print("❌ 未设置 DISCORD_TOKEN")
        sys.exit(1)

    headless_mode = os.environ.get("HEADLESS", "true").lower() == "true"
    print(f"🖥️  {'无头' if headless_mode else '有头'}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless_mode,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            timezone_id="America/New_York",
        )
        context.add_init_script(STEALTH_JS)
        page = context.new_page()
        _install_ws_hook(page)

        try:
            if not login_with_discord_token(page, DISCORD_TOKEN):
                print("\n❌ 登录失败")
                send_telegram_message("❌ 登录流程失败")
                return

            vps_list = get_vps_urls(page)
            if not vps_list:
                print("\n❌ 未找到 VPS")
                save_screenshot(page, "no_vps_found")
                send_telegram_message("❌ 未在面板找到 VPS 实例")
                return

            for idx, target_url in enumerate(vps_list, 1):
                print(f"\n{'=' * 50}")
                print(f"📌 [{idx}/{len(vps_list)}] {target_url}")
                print(f"{'=' * 50}")

                _reset_ws_state()

                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    print(f"⚠️ 加载: {e}")
                wait_for_cloudflare(page)
                page.wait_for_timeout(3000)

                if "/login" in page.url:
                    print("❌ 被重定向到登录页")
                    save_screenshot(page, f"redirect_to_login_{idx}")
                    send_telegram_message("❌ 登录后仍被重定向")
                    break

                try:
                    page_text = page.locator("body").inner_text()
                except Exception:
                    page_text = ""

                if "404" in page.title() or "Page Not Found" in page.title():
                    print(f"❌ 404: {target_url}")
                    continue

                print("✅ 到达 VPS 页面")
                save_screenshot(page, f"vps_page_loaded_{idx}")

                m = re.search(r"[Rr]enews?\s+in\s+(\d+)\s+days?", page_text)
                if m:
                    days_left = int(m.group(1))
                    print(f"🔍 剩余: {days_left} 天")
                    if days_left > RENEW_THRESHOLD_DAYS:
                        print(f"⏳ 跳过续期")
                        send_telegram_message(
                            f"ℹ️ 无需续期\n实例: {target_url}\n剩余: {days_left} 天")
                        continue
                    print(f"⚠️ {days_left} ≤ {RENEW_THRESHOLD_DAYS}，开始续期")
                else:
                    print("⚠️ 未解析到剩余天数，强制尝试")
                    days_left = 0

                print(f"\n{'=' * 50}\n🔄 开始验证码续期\n{'=' * 50}")
                ok = try_renew_captcha(page, initial_days=days_left)

                if ok:
                    expiry = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=6)
                    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S") + " (GMT+8)"
                    print(f"✅ 续期成功，至: {expiry_str}")
                    send_telegram_message(
                        f"✅ 续期成功！\n实例: {target_url}\n续期至: {expiry_str}")
                else:
                    print("❌ 续期失败")
                    send_telegram_message(
                        f"❌ 续期失败\n实例: {target_url}")

        except Exception as e:
            print(f"\n💥 异常: {e}")
            import traceback
            traceback.print_exc()
            try:
                save_screenshot(page, "uncaught_error")
            except Exception:
                pass
            send_telegram_message(f"❌ 异常: {str(e)[:200]}")

        finally:
            browser.close()
            print("\n🏁 执行完毕")


if __name__ == "__main__":
    main()
