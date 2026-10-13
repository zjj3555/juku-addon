#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
juku_client.py —— 剧库(jukuku.xyz) API 客户端，纯 Python，无 PC 依赖。

完整复刻电脑版「剧库」App 的设备签名鉴权：
  - 用 ~/.mvmultiplatform/device.pri (EC P-256 私钥) 做 ECDSA-SHA256 签名
  - X-Signature = Base64( DER->P1363(raw 64字节 r||s) )
  - 首次/过期时用 /auth/device/register (公钥) 拿 deviceId + accessToken + refreshToken
  - 之后所有 /videos/* 请求走同样签名

本模块可在:
  - 本地直接跑 (python juku_client.py 自检)
  - Netlify Function 里 import (从环境变量 JUKU_DEVICE_PRI 取私钥 base64)
"""

import os
import re
import sys
import json
import time
import base64
import secrets
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import ssl

from cryptography.hazmat.primitives.serialization import (
    load_der_private_key,
    load_pem_private_key,
    Encoding,
    PublicFormat,
)
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes

BASE_URL = "https://api.jukuku.xyz"
APP_VERSION = "1.15.0"
PLATFORM = "desktop"
INSTALL_ID = "juku-netlify-addon"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


# ----------------------------------------------------------------------------
# 私钥加载
# ----------------------------------------------------------------------------
def load_private_key():
    raw = os.environ.get("JUKU_DEVICE_PRI", "").strip()
    if raw:
        # 支持 base64(DER) / base64(PEM) / 纯 PEM
        try:
            data = base64.b64decode(raw, validate=True)
        except Exception:
            data = raw.encode("utf-8")
        # 判断是否为 PEM
        if b"-----BEGIN" in data:
            return load_pem_private_key(data, password=None)
        return load_der_private_key(data, password=None)
    # 本地回退路径
    for cand in [
        os.path.expanduser(r"~/.mvmultiplatform/device.pri"),
        r"C:\Users\Administrator\.mvmultiplatform\device.pri",
    ]:
        if os.path.exists(cand):
            with open(cand, "rb") as f:
                data = f.read()
            if b"-----BEGIN" in data:
                return load_pem_private_key(data, password=None)
            return load_der_private_key(data, password=None)
    raise RuntimeError("找不到 device.pri 私钥，请设置 JUKU_DEVICE_PRI 环境变量")


# ----------------------------------------------------------------------------
# 签名原语 (完全复刻 com.mv.app.data.auth.*)
# ----------------------------------------------------------------------------
def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _percent_encode(value: str) -> str:
    out = []
    for b in value.encode("utf-8"):
        c = chr(b)
        if ("A" <= c <= "Z") or ("a" <= c <= "z") or ("0" <= c <= "9") or c in "-._~":
            out.append(c)
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def canonical_query(params) -> str:
    """params: list of (key,value) 或 dict。按 key 排序后 percentEncode 拼接。"""
    if isinstance(params, dict):
        items = list(params.items())
    else:
        items = list(params)
    items = sorted(items, key=lambda kv: kv[0])
    return "&".join(_percent_encode(k) + "=" + _percent_encode(str(v)) for k, v in items)


def signing_string(method, path, canon_q, body_hash, ts, nonce, device_id, access_token) -> str:
    return "\n".join([
        method.upper(), path, canon_q, body_hash,
        ts, nonce, device_id, access_token,
    ])


def _der_to_p1363(der: bytes, part_len: int = 32) -> bytes:
    """Java Signature.sign() 返回 DER，转换为 P1363 raw r(32)||s(32)。"""
    assert der[0] == 0x30
    pos = 1
    seq_len = der[pos]
    pos += 1
    _end = pos + seq_len

    def read_int():
        nonlocal pos
        assert der[pos] == 0x02
        pos += 1
        ln = der[pos]
        pos += 1
        val = der[pos:pos + ln]
        pos += ln
        return val

    r = read_int()
    s = read_int()
    norm = lambda v, n: v.lstrip(b"\x00").rjust(n, b"\x00")
    return norm(r, part_len) + norm(s, part_len)


def sign_base64(private_key, ss: str) -> str:
    sig = private_key.sign(ss.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(_der_to_p1363(sig, 32)).decode("ascii")


def public_key_b64(private_key) -> str:
    pub = private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(pub).decode("ascii")


# ----------------------------------------------------------------------------
# 带鉴权的 HTTP 客户端
# ----------------------------------------------------------------------------
class JukuClient:
    def __init__(self, private_key=None):
        self.private_key = private_key or load_private_key()
        self._session = None  # {deviceId, accessToken, accessExpiresAt, refreshToken, refreshExpiresAt}
        self._try_load_cache()

    # ---- 会话持久化 (尽量复用 token, 减少 register 调用) ----
    def _cache_path(self):
        return os.environ.get("JUKU_SESSION_CACHE", "/tmp/juku_session.json")

    def _try_load_cache(self):
        try:
            p = self._cache_path()
            if os.path.exists(p):
                with open(p) as f:
                    self._session = json.load(f)
        except Exception:
            self._session = None

    def _save_cache(self):
        try:
            with open(self._cache_path(), "w") as f:
                json.dump(self._session, f)
        except Exception:
            pass

    def _token_valid(self):
        if not self._session:
            return False
        exp = self._session.get("accessExpiresAt", 0)
        return time.time() * 1000 < (exp - 30_000)  # 提前 30s 过期

    # ---- 注册 / 刷新 ----
    def ensure_session(self):
        if self._token_valid():
            return self._session
        # 有 refreshToken 且未过期 -> refresh
        if self._session and self._session.get("refreshExpiresAt", 0) > time.time() * 1000:
            try:
                self._refresh()
                return self._session
            except Exception:
                pass
        # 否则重新注册 (用同一把 device.pri 公钥)
        self._register()
        return self._session

    def _register(self):
        body = json.dumps({
            "publicKey": public_key_b64(self.private_key),
            "appVersion": APP_VERSION,
            "platform": PLATFORM,
            "installId": INSTALL_ID,
        }).encode("utf-8")
        h = self._headers("POST", "/auth/device/register", body, "", "")
        resp = self._raw("POST", "/auth/device/register", body, h)
        data = resp["data"]
        self._session = {
            "deviceId": data["deviceId"],
            "accessToken": data["accessToken"],
            "accessExpiresAt": self._iso_to_epoch_ms(data.get("accessExpiresAt")),
            "refreshToken": data["refreshToken"],
            "refreshExpiresAt": self._iso_to_epoch_ms(data.get("refreshExpiresAt")),
        }
        self._save_cache()

    def _refresh(self):
        body = json.dumps({
            "deviceId": self._session["deviceId"],
            "refreshToken": self._session["refreshToken"],
        }).encode("utf-8")
        h = self._headers("POST", "/auth/device/refresh", body,
                          self._session["deviceId"], self._session["accessToken"])
        resp = self._raw("POST", "/auth/device/refresh", body, h)
        data = resp["data"]
        self._session.update({
            "accessToken": data["accessToken"],
            "accessExpiresAt": self._iso_to_epoch_ms(data.get("accessExpiresAt")),
            "refreshToken": data.get("refreshToken", self._session["refreshToken"]),
            "refreshExpiresAt": self._iso_to_epoch_ms(data.get("refreshExpiresAt")),
        })
        self._save_cache()

    @staticmethod
    def _iso_to_epoch_ms(s):
        if not s:
            return 0
        # 形如 2026-10-13T07:51:55.777Z
        s2 = s.replace("Z", "+00:00")
        try:
            import datetime
            dt = datetime.datetime.fromisoformat(s2)
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    # ---- 通用请求 ----
    def _headers(self, method, path, body, device_id, access_token):
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        bh = _sha256_hex(body)
        ss = signing_string(method, path, canonical_query([]), bh, ts, nonce, device_id, access_token)
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Device-Id": device_id,
            "X-App-Version": APP_VERSION,
            "X-Timestamp": ts,
            "X-Nonce": nonce,
            "X-Body-Hash": bh,
            "X-Access-Token": access_token,
            "X-Signature": sign_base64(self.private_key, ss),
        }

    def _raw(self, method, path, body, headers, params=None):
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            r = urllib.request.urlopen(req, timeout=15, context=CTX)
            return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")))

    def request(self, method, path, params=None, body=b""):
        self.ensure_session()
        if isinstance(body, str):
            body = body.encode("utf-8")
        # 签名必须用 canonicalQuery(由 params 推导)，且实际 URL 也用同一套
        canon_q = canonical_query(list(params.items())) if params else ""
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        bh = _sha256_hex(body)
        ss = signing_string(method, path, canon_q, bh, ts, nonce,
                            self._session["deviceId"], self._session["accessToken"])
        h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Device-Id": self._session["deviceId"],
            "X-App-Version": APP_VERSION,
            "X-Timestamp": ts,
            "X-Nonce": nonce,
            "X-Body-Hash": bh,
            "X-Access-Token": self._session["accessToken"],
            "X-Signature": sign_base64(self.private_key, ss),
        }
        return self._raw(method, path, body, h, params)

    # ---- 业务 API ----
    def get_home(self):
        return self.request("GET", "/videos/getHomeData")

    def get_video_list(self, page=1, page_size=30, type_id=None, area=None, year=None):
        params = {"page": page, "pageSize": page_size}
        if type_id is not None:
            params["typeId"] = type_id
        if area:
            params["area"] = area
        if year:
            params["year"] = year
        return self.request("GET", "/videos", params=params)

    def get_video_detail(self, video_id):
        return self.request("GET", "/videos/%s" % video_id)

    def search(self, q, page=1, page_size=30):
        return self.request("GET", "/videos/search",
                            params={"q": q, "page": page, "pageSize": page_size})

    def get_tags(self):
        return self.request("GET", "/videos/tags")

    def get_recommend(self, video_id=None):
        path = "/videos/recommend" + ("/%s" % video_id if video_id else "")
        return self.request("GET", path)


# ----------------------------------------------------------------------------
# 自检 (本地直接 python juku_client.py)
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    c = JukuClient()
    print("== ensure_session ==")
    s = c.ensure_session()
    print("deviceId:", s["deviceId"], "token:", s["accessToken"][:20], "...")
    print("== getHomeData ==")
    hd = c.get_home()
    print("home success:", hd.get("success"), "bannerVideos:", len(hd["data"].get("bannerVideos", [])))
    print("== getVideoDetail(215231) playListByLine ==")
    det = c.get_video_detail("215231")
    pl = det["data"]["playListByLine"]
    print("lines:", list(pl.keys())[:5])
    first = pl[list(pl.keys())[0]][0]
    print("sample url:", first.get("url"))
