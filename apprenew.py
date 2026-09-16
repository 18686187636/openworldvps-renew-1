#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import io
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


# ================= 反自动化 + WebSocket Hook =================
STEALTH_JS = r"""
(function() {
  try { Object.defineProperty(navigator, 'webdriver', { get: () => false }); } catch(e) {}
  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => [
        { name: 'PDF Viewer' }, { name: 'Chrome PDF Viewer' },
        { name: 'Chromium PDF Viewer' }, { name: 'WebKit built-in PDF' }
      ]
    });
  } catch(e) {}
  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en', 'zh-CN'] });
  } catch(e) {}
  try {
    if (!window.chrome) {
      window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
    }
  } catch(e) {}
  ['__selenium_unwrapped','__webdriver_evaluate','__selenium_evaluate',
   '__driver_evaluate','__fxdriver_evaluate','_Selenium_IDE_Recorder',
   '__webdriver_script_fn','__webdriver_script_func','__webdriver_script_url',
   '__driver_script_fn','__driver_script_url','_phantom','__nightmare',
   'callPhantom','domAutomation','domAutomationController'].forEach(function(k) {
    try { delete window[k]; } catch(e) {}
  });
  try {
    for (var k in window) {
      if (k.indexOf('$cdc_') === 0) { try { delete window[k]; } catch(e) {} }
    }
  } catch(e) {}
  try { delete document.$cdc_asdjflasutopfhvcZLmcfl_; } catch(e) {}

  // ---- Hook WebSocket ----
  var OrigWS = window.WebSocket;
  window.__ow = { meta: null, frames: [], lastResp: null, sent: [],
                  closed: false, closeCode: null };

  function OWWS(url, protocols) {
    var ws = (protocols === undefined) ? new OrigWS(url) : new OrigWS(url, protocols);
    ws.addEventListener('message', function(ev) {
      if (typeof ev.data === 'string') {
        window.__ow.lastResp = ev.data;
        try {
          var m = JSON.parse(ev.data);
          if (m && m.id && m.nf) {
            window.__ow.meta = m;
            window.__ow.frames = [];
          }
        } catch(e) {}
      } else {
        (function(d) {
          (function() {
            if (d instanceof ArrayBuffer) return Promise.resolve(d);
            return d.arrayBuffer();
          })().then(function(ab) {
            var bytes = new Uint8Array(ab);
            var bin = '';
            var CH = 0x8000;
            for (var i = 0; i < bytes.length; i += CH) {
              bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
            }
            window.__ow.frames.push(btoa(bin));
          }).catch(function(){});
        })(ev.data);
      }
    });
    ws.addEventListener('close', function(e) {
      window.__ow.closed = true;
      window.__ow.closeCode = e.code;
    });
    var origSend = ws.send.bind(ws);
    ws.send = function(d) {
      try { window.__ow.sent.push(typeof d === 'string' ? d : '[bin]'); } catch(e) {}
      return origSend(d);
    };
    return ws;
  }
  OWWS.prototype = OrigWS.prototype;
  OWWS.CONNECTING = 0; OWWS.OPEN = 1; OWWS.CLOSING = 2; OWWS.CLOSED = 3;
  window.WebSocket = OWWS;
})();
"""


# ================= 通用工具 =================

def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    full_message = f"👤 账号: {ACCOUNT_NAME}\n{message}"
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": full_message}, timeout=10)
        if resp.status_code == 200:
            print("✅ Telegram 通知已发送")
        else:
            print(f"❌ Telegram 发送失败: HTTP {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Telegram 发送异常: {e}")


def save_screenshot(page, name: str):
    try:
        path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
        page.screenshot(path=path, full_page=False)
        print(f"   📸 截图已保存: {path}")
    except Exception as e:
        print(f"   ⚠️ 截图失败: {e}")


def dump_page_debug(page, name: str):
    save_screenshot(page, name)
    try:
        html_path = os.path.join(SCREENSHOT_DIR, f"{name}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"   📄 HTML 已保存: {html_path}")
    except Exception as e:
        print(f"   ⚠️ HTML 保存失败: {e}")


def wait_for_cloudflare(page, timeout=15):
    cf_indicators = ["verify you are human", "just a moment", "checking your browser",
                     "cf-browser-verification", "challenge-platform"]
    start = time.time()
    while time.time() - start < timeout:
        try:
            content = page.content().lower()
            if not any(ind in content for ind in cf_indicators):
                return True
        except Exception:
            pass
        time.sleep(1)
    print("⚠️ Cloudflare 挑战等待超时")
    return False


# ================= Discord OAuth 登录 =================

def login_with_discord_token(page, dc_token: str) -> bool:
    print("=" * 50)
    print("🔑 开始 Discord OAuth 登录流程")
    print("=" * 50)

    print("\n📌 第1步：访问首页建立基础 Cookie/Session")
    try:
        page.goto(SITE_BASE, wait_until="domcontentloaded", timeout=30000)
        wait_for_cloudflare(page)
        time.sleep(2)
        print(f"   首页加载完成，URL: {page.url}")
    except Exception as e:
        print(f"   ⚠️ 首页加载异常: {e}")

    login_url = f"{SITE_BASE}/login"
    print(f"\n📌 第2步：访问登录页: {login_url}")
    try:
        page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
        wait_for_cloudflare(page)
        time.sleep(3)
        print(f"   URL: {page.url}")
    except Exception as e:
        print(f"   ⚠️ 登录页异常: {e}")

    current_url = page.url
    print(f"\n📌 第3步：检查是否到达 Discord OAuth")

    if "discord.com" not in current_url:
        print("   未自动跳转，尝试点击登录按钮...")
        btn_selectors = [
            "button:has-text('Sign in with Discord')",
            "a:has-text('Sign in with Discord')",
            "button:has-text('Discord')",
            "a:has-text('Discord')",
            "button:has-text('登录')",
            "a:has-text('登录')",
        ]
        for selector in btn_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    print(f"   点击: '{btn.inner_text().strip()}'")
                    btn.click()
                    time.sleep(5)
                    if "discord.com" in page.url:
                        break
            except Exception:
                continue

        if "discord.com" not in page.url:
            print("   尝试从源码提取 OAuth 链接...")
            try:
                html = page.content()
                m = re.search(r'https://discord\.com/oauth2/authorize[^\s"\'<>]+', html)
                if m:
                    page.goto(m.group(0), wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
            except Exception as e:
                print(f"   ⚠️ {e}")

    if "discord.com" not in page.url:
        for _ in range(10):
            time.sleep(1)
            if "discord.com" in page.url:
                break

    if "discord.com" not in page.url:
        print(f"   ❌ 未跳转到 Discord，URL: {page.url}")
        save_screenshot(page, "login_failed_no_discord")
        return False

    print(f"   ✅ 已到 Discord OAuth")

    print(f"\n📌 第4步：解析 OAuth 参数")
    oauth_url = page.url
    if "discord.com/login" in oauth_url and "redirect_to=" in oauth_url:
        parsed_login = urllib.parse.urlparse(oauth_url)
        login_params = urllib.parse.parse_qs(parsed_login.query)
        redirect_to = login_params.get("redirect_to", [""])[0]
        if redirect_to:
            oauth_url = ("https://discord.com" + redirect_to) if redirect_to.startswith("/") else redirect_to

    parsed = urllib.parse.urlparse(oauth_url)
    params = urllib.parse.parse_qs(parsed.query)
    client_id     = params.get("client_id", [""])[0]
    redirect_uri  = params.get("redirect_uri", [""])[0]
    scope         = params.get("scope", ["identify email"])[0]
    state         = params.get("state", [""])[0]
    response_type = params.get("response_type", ["code"])[0]
    access_type   = params.get("access_type", [""])[0]
    prompt        = params.get("prompt", [""])[0]

    print(f"   Client ID:    {client_id}")
    print(f"   Redirect URI: {redirect_uri}")
    print(f"   Scope:        {scope}")

    if not client_id or not redirect_uri:
        print("   ❌ 无法解析 OAuth 参数")
        save_screenshot(page, "login_failed_parse")
        return False

    print(f"\n📌 第5步：Discord API 完成授权")
    api_params_dict = {
        "client_id": client_id,
        "response_type": response_type,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if access_type: api_params_dict["access_type"] = access_type
    if prompt:      api_params_dict["prompt"] = prompt

    api_params = urllib.parse.urlencode(api_params_dict)
    authorize_api = f"https://discord.com/api/v9/oauth2/authorize?{api_params}"
    referer = f"https://discord.com/oauth2/authorize?{api_params}"

    headers = {
        "accept": "*/*",
        "authorization": dc_token.strip(),
        "content-type": "application/json",
        "origin": "https://discord.com",
        "referer": referer,
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
        "x-discord-locale": "zh-CN",
    }
    body = {
        "permissions": "0",
        "authorize": True,
        "integration_type": 0,
    }

    try:
        resp = requests.post(authorize_api, headers=headers, json=body, timeout=20)
        print(f"   API 状态码: {resp.status_code}")
        if resp.status_code in (401, 403):
            print("   ❌ Discord Token 失效或权限不足")
            return False
        if resp.status_code != 200:
            print(f"   ❌ 授权失败: {resp.text[:300]}")
            return False
        resp_data = resp.json()
    except Exception as e:
        print(f"   ❌ API 异常: {e}")
        return False

    location = resp_data.get("location", "")
    if not location:
        print(f"   ❌ 无 location 字段: {json.dumps(resp_data)[:300]}")
        return False

    print(f"   ✅ 拿到回调 URL: {re.sub(r'code=[^&]+', 'code=***', location)[:120]}")

    print(f"\n📌 第6步：回调 URL 完成登录")
    try:
        page.goto(location, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"   ⚠️ 回调加载异常（可能正常）: {e}")
    time.sleep(5)
    wait_for_cloudflare(page)

    final_url = page.url
    if "/login" in final_url and "discord" not in final_url:
        time.sleep(5)
        final_url = page.url
        if "/login" in final_url:
            print(f"   ❌ 登录失败，停留在: {final_url}")
            save_screenshot(page, "login_callback_stuck")
            return False

    if "openworld.eu.org" in final_url:
        print(f"   ✅ 登录成功: {final_url}")
        save_screenshot(page, "login_success")
        return True

    print(f"   ⚠️ 登录状态不确定: {final_url}")
    return True


# ================= 拼图滑块验证码 =================

def _reset_ow(page):
    try:
        page.evaluate("""() => {
            if (!window.__ow) window.__ow = {};
            window.__ow.frames = [];
            window.__ow.lastResp = null;
            window.__ow.sent = [];
            window.__ow.closed = false;
            window.__ow.closeCode = null;
        }""")
    except Exception:
        pass


def _wait_captcha_meta(page, timeout=15):
    page.wait_for_function(
        "() => window.__ow && window.__ow.meta && window.__ow.meta.id",
        timeout=timeout * 1000,
    )


def _wait_captcha_frames(page, timeout=15):
    page.wait_for_function(
        """() => {
            const o = window.__ow;
            return o && o.meta && o.frames && o.frames.length >= o.meta.nf;
        }""",
        timeout=timeout * 1000,
    )


def _read_ow_state(page):
    return page.evaluate("""() => ({
        meta: window.__ow.meta,
        frames: window.__ow.frames,
        lastResp: window.__ow.lastResp,
        sent: window.__ow.sent,
        closed: window.__ow.closed,
        closeCode: window.__ow.closeCode,
    })""")


def _solve_puzzle(bg_bytes: bytes, piece_bytes: bytes, meta: dict) -> int:
    """返回拼图块在 bg 坐标系中的目标 x。"""
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_COLOR)
    piece = cv2.imdecode(np.frombuffer(piece_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if bg is None or piece is None:
        raise RuntimeError("图片解码失败")

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    vmax = int(meta.get("vmax") or bg.shape[1])

    # ---- 方法1：alpha 掩码模板匹配 ----
    if piece.ndim == 3 and piece.shape[2] == 4:
        alpha = piece[:, :, 3]
        ys, xs = np.where(alpha > 100)
        if len(xs) > 0:
            x0, x1 = int(xs.min()), int(xs.max()) + 1
            y0, y1 = int(ys.min()), int(ys.max()) + 1
            piece_rgb = piece[y0:y1, x0:x1, :3]
            piece_mask = alpha[y0:y1, x0:x1]
            piece_gray = cv2.cvtColor(piece_rgb, cv2.COLOR_BGR2GRAY)
            try:
                res = cv2.matchTemplate(bg_gray, piece_gray,
                                        cv2.TM_CCORR_NORMED, mask=piece_mask)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                print(f"   🧪 模板匹配 max_val={max_val:.3f} loc={max_loc}")
                if max_val > 0.5:
                    v = int(max_loc[0]) - x0
                    return max(0, min(vmax, v))
            except Exception as e:
                print(f"   ⚠️ 模板匹配异常，改用边缘法: {e}")

    # ---- 方法2：Canny 边缘重叠 ----
    piece_rgb = piece[:, :, :3] if piece.ndim == 3 else piece
    piece_gray = cv2.cvtColor(piece_rgb, cv2.COLOR_BGR2GRAY)
    piece_edges = cv2.Canny(piece_gray, 50, 150)
    bg_edges = cv2.Canny(bg_gray, 50, 150)

    py = int(meta.get("py", 0))
    ph, pw = piece_edges.shape
    H, W = bg_edges.shape
    if py + ph > H:
        py = max(0, H - ph)

    best_x, best_score = 0, -1
    for x in range(0, max(1, W - pw + 1)):
        region = bg_edges[py:py + ph, x:x + pw]
        if region.shape != piece_edges.shape:
            continue
        score = int(np.sum((region > 0) & (piece_edges > 0)))
        if score > best_score:
            best_score, best_x = score, x

    print(f"   🧪 边缘匹配 best_x={best_x} score={best_score}")
    return max(0, min(vmax, best_x))


def _drag_slider(page, value: int, vmax: int):
    """拖动 track 滑块到 value；mouse.up 会触发组件 submitSolution。"""
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
    start_x = box["x"] + handle_w / 2 + 0.02 * usable

    page.mouse.move(start_x, y)
    time.sleep(random.uniform(0.05, 0.15))
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.12))

    steps = random.randint(20, 32)
    for i in range(1, steps + 1):
        t = i / steps
        eased = 1 - (1 - t) ** 2
        x = start_x + (target_x - start_x) * eased
        yy = y + random.uniform(-1.5, 1.5)
        page.mouse.move(x, yy)
        time.sleep(random.uniform(0.008, 0.022))

    for _ in range(3):
        page.mouse.move(target_x + random.uniform(-0.7, 0.7),
                        y + random.uniform(-1.2, 1.2))
        time.sleep(random.uniform(0.02, 0.05))

    page.mouse.up()


def _wait_captcha_result(page, timeout=12):
    try:
        page.wait_for_function(
            """() => {
                const r = window.__ow.lastResp || '';
                return r.startsWith('ok:') || r === 'fail' || r === 'burned' ||
                       r === 'blocked' || r.startsWith('bot:') || r === 'rate';
            }""",
            timeout=timeout * 1000,
        )
    except Exception:
        return None
    return page.evaluate("() => window.__ow.lastResp")


def _try_renew_once(page, attempt: int, initial_days: int):
    """单次尝试。返回 True=成功 / False=应放弃 / None=继续重试。"""
    print(f"\n   {'='*40}\n   🔄 第 {attempt} 次尝试\n   {'='*40}")

    # 1. 点击 Renew free
    try:
        btn = page.locator("button:has-text('Renew free')").first
        btn.wait_for(state="visible", timeout=8000)
        btn.click()
        print("   ✅ 已点击 Renew free")
    except Exception as e:
        print(f"   ❌ 未找到续期按钮: {e}")
        return None

    # 2. 重置 hook
    _reset_ow(page)

    # 3. 等 meta
    try:
        _wait_captcha_meta(page, timeout=15)
    except Exception as e:
        print(f"   ❌ 等待 captcha meta 超时: {e}")
        dump_page_debug(page, f"no_meta_{attempt}")
        return None

    # 4. 等帧
    try:
        _wait_captcha_frames(page, timeout=15)
    except Exception as e:
        print(f"   ❌ 等待 captcha frames 超时: {e}")
        return None

    st = _read_ow_state(page)
    meta = st["meta"]
    kind = meta.get("kind")
    nf = meta.get("nf")
    print(f"   📋 kind={kind} nf={nf} w={meta.get('w')} h={meta.get('h')} "
          f"vmax={meta.get('vmax')} pw={meta.get('pw')} py={meta.get('py')}")

    frames = [base64.b64decode(b) for b in st["frames"]]
    for i, fb in enumerate(frames):
        try:
            with open(os.path.join(SCREENSHOT_DIR,
                                   f"captcha_a{attempt}_f{i}.png"), "wb") as f:
                f.write(fb)
        except Exception:
            pass

    if kind != "puzzle":
        print(f"   ⚠️ 本轮 kind={kind}，暂只实现 puzzle")
        return None
    if nf < 2:
        print(f"   ⚠️ nf={nf}，缺少拼图块帧")
        return None

    # 5. 求解
    try:
        value = _solve_puzzle(frames[0], frames[1], meta)
        print(f"   🧩 缺口估算 value={value} / vmax={meta.get('vmax')}")
    except Exception as e:
        print(f"   ❌ 拼图求解失败: {e}")
        return None

    # 6. 拖动
    try:
        _drag_slider(page, value, int(meta.get("vmax") or 300))
    except Exception as e:
        print(f"   ❌ 拖动滑块失败: {e}")
        return None

    # 7. 读响应
    resp = _wait_captcha_result(page, timeout=12)
    print(f"   📨 服务端响应: {resp!r}")
    if not resp or not resp.startswith("ok:"):
        return None

    token = resp[3:]
    print(f"   ✅ 验证码通过，token 长度={len(token)}")

    # 8. 确认续期
    try:
        confirm = page.locator("button:has-text('Confirm Renewal')").first
        confirm.wait_for(state="visible", timeout=5000)
        confirm.click()
        print("   ✅ 已点击 Confirm Renewal")
    except Exception as e:
        print(f"   ⚠️ 点击 Confirm Renewal 异常: {e}")

    # 9. 验证
    time.sleep(4)
    try:
        page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    wait_for_cloudflare(page)
    time.sleep(2)

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
        print(f"   ❌ 未增加（{initial_days} → {new_days}）")
        return None

    print("   ⚠️ 刷新后无法解析天数")
    save_screenshot(page, f"after_renew_a{attempt}")
    return None


def try_renew_captcha(page, initial_days: int, max_attempts=5) -> bool:
    for attempt in range(1, max_attempts + 1):
        try:
            result = _try_renew_once(page, attempt, initial_days)
        except Exception as e:
            print(f"   ❌ 第 {attempt} 次尝试异常: {e}")
            import traceback
            traceback.print_exc()
            result = None

        if result is True:
            return True

        if attempt < max_attempts:
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
                page.reload(wait_until="domcontentloaded", timeout=30000)
                wait_for_cloudflare(page)
                time.sleep(3)
            except Exception as e:
                print(f"   ⚠️ 刷新异常: {e}")

    print(f"   ❌ {max_attempts} 次尝试均失败")
    return False


# ================= VPS 列表提取 =================

def get_vps_urls(page) -> list:
    vps_urls = []

    def extract():
        found = []
        try:
            links = page.locator("a[href*='/vps/']").all()
            for link in links:
                href = link.get_attribute("href") or ""
                if not href:
                    continue
                full_url = urllib.parse.urljoin(SITE_BASE, href)
                path = urllib.parse.urlparse(full_url).path.rstrip('/')
                parts = [p for p in path.split('/') if p]
                # 只保留 /vps/<uuid> 这种形态，排除 /vps、/vps/list、/vps/new 等
                if len(parts) == 2 and parts[0] == "vps" and \
                   parts[1] not in ("list", "new", "create"):
                    if full_url not in found:
                        found.append(full_url)
        except Exception as e:
            print(f"   ⚠️ 提取 VPS 链接异常: {e}")
        return found

    print("\n🔍 自动识别账号下 VPS 实例...")
    vps_urls = extract()

    if not vps_urls:
        try:
            print(f"   前往首页 {SITE_BASE} 提取...")
            page.goto(SITE_BASE, wait_until="domcontentloaded", timeout=30000)
            wait_for_cloudflare(page)
            time.sleep(3)
            vps_urls = extract()
        except Exception as e:
            print(f"   ⚠️ 首页提取失败: {e}")

    if not vps_urls:
        for sub in ["/dashboard", "/vps"]:
            try:
                print(f"   尝试 {SITE_BASE}{sub} ...")
                page.goto(f"{SITE_BASE}{sub}", wait_until="domcontentloaded", timeout=30000)
                wait_for_cloudflare(page)
                time.sleep(3)
                vps_urls = extract()
                if vps_urls:
                    break
            except Exception:
                pass

    if vps_urls:
        print(f"   ✅ 检测到 {len(vps_urls)} 个 VPS:")
        for u in vps_urls:
            print(f"      - {u}")
    else:
        print("   ❌ 未检测到任何 VPS 实例")

    return vps_urls


# ================= 主流程 =================

def main():
    print("#" * 50)
    print("   Openworld VPS 自动续期脚本 (v2 - WebSocket 拼图版)")
    print("#" * 50)

    if not DISCORD_TOKEN:
        print("❌ 未设置 DISCORD_TOKEN")
        sys.exit(1)

    headless_mode = os.environ.get("HEADLESS", "true").lower() == "true"
    print(f"🖥️  运行模式: {'无头' if headless_mode else '有头'}")

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

        try:
            success = login_with_discord_token(page, DISCORD_TOKEN)
            if not success:
                print("\n❌ 登录失败，退出")
                send_telegram_message("❌ Openworld VPS 续期失败：登录流程失败")
                return

            target_vps_list = get_vps_urls(page)
            if not target_vps_list:
                print("\n❌ 未检测到 VPS 实例")
                save_screenshot(page, "no_vps_found")
                send_telegram_message("❌ Openworld VPS 续期失败：未在面板找到任何 VPS 实例")
                return

            for idx, target_url in enumerate(target_vps_list, 1):
                print(f"\n{'=' * 50}")
                print(f"📌 [{idx}/{len(target_vps_list)}] {target_url}")
                print(f"{'=' * 50}")

                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    print(f"⚠️ 页面加载异常: {e}")
                wait_for_cloudflare(page)
                time.sleep(3)

                current_url = page.url
                page_title = page.title()
                print(f"📝 URL: {current_url}")
                print(f"📝 标题: {page_title}")

                if "/login" in current_url:
                    print("❌ 被重定向到登录页")
                    save_screenshot(page, f"redirect_to_login_{idx}")
                    send_telegram_message("❌ 登录后仍被重定向到登录页")
                    break

                try:
                    page_text = page.locator("body").inner_text()
                except Exception:
                    page_text = ""

                if "404" in page_title or "Page Not Found" in page_title:
                    print(f"❌ 页面 404: {target_url}")
                    save_screenshot(page, f"vps_404_{idx}")
                    send_telegram_message(f"❌ 页面 404: {target_url}")
                    continue

                print("✅ 已到达 VPS 页面")
                save_screenshot(page, f"vps_page_loaded_{idx}")

                match = re.search(r"[Rr]enews?\s+in\s+(\d+)\s+days?", page_text)
                if match:
                    days_left = int(match.group(1))
                    print(f"🔍 剩余续期时间: {days_left} 天")
                    if days_left > RENEW_THRESHOLD_DAYS:
                        print(f"⏳ {days_left} > {RENEW_THRESHOLD_DAYS}，跳过续期")
                        send_telegram_message(
                            f"ℹ️ 无需续期\n实例: {target_url}\n剩余: {days_left} 天")
                        continue
                    print(f"⚠️ {days_left} ≤ {RENEW_THRESHOLD_DAYS}，开始续期...")
                else:
                    print("⚠️ 未提取到剩余天数，强制尝试")
                    days_left = 0

                print(f"\n{'=' * 50}\n🔄 开始验证码续期\n{'=' * 50}")
                renew_success = try_renew_captcha(page, initial_days=days_left)

                if renew_success:
                    expiry = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=6)
                    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S") + " (GMT+8)"
                    msg = (f"✅ Openworld VPS 续期成功！\n"
                           f"实例: {target_url}\n"
                           f"续期至: {expiry_str}")
                    print(f"✅ 续期成功，续期至: {expiry_str}")
                    send_telegram_message(msg)
                else:
                    print("❌ 续期失败")
                    send_telegram_message(
                        f"❌ Openworld VPS 续期失败：5 次尝试均未成功\n实例: {target_url}")

        except Exception as e:
            print(f"\n💥 未捕获异常: {e}")
            import traceback
            traceback.print_exc()
            try:
                save_screenshot(page, "uncaught_error")
            except Exception:
                pass
            send_telegram_message(f"❌ 续期脚本异常: {str(e)[:200]}")

        finally:
            browser.close()
            print("\n🏁 脚本执行完毕")


if __name__ == "__main__":
    main()
