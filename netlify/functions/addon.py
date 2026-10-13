#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Netlify Function —— 动态 juku Stremio 插件 (零 PC 依赖)。

Stremio 安装地址:
  https://<你的site>/.netlify/functions/addon/manifest.json

路由:
  /manifest.json                      -> 插件清单
  /catalog/movie/<catId>.json         -> 片单 (支持 skip 翻页)
  /meta/movie/<id>.json               -> 单部详情(供 Stremio 展示)
  /stream/movie/<id>.json             -> 真实 HLS 直链 (playListByLine)

所有 juku 签名/注册都在本函数内用内置 device.pri (环境变量 JUKU_DEVICE_PRI) 完成。
"""

import os
import json
import traceback
from urllib.parse import urlencode

# 复用同目录的 juku_client
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from juku_client import JukuClient

MANIFEST = {
    "id": "community.juku.hls",
    "version": "2.0.0",
    "name": "Juku 剧库 (HLS 实时)",
    "description": "实时解析剧库 jukuku.xyz 的免登录 HLS 直链，SwitchStream / nxmp / Stremio 通用。服务端解析，PC 无需常开。",
    "types": ["movie", "series"],
    "resources": [
        {"name": "catalog", "types": ["movie", "series"],
         "idPrefixes": ["juku-"]},
        {"name": "meta", "types": ["movie", "series"],
         "idPrefixes": ["juku-"]},
        {"name": "stream", "types": ["movie", "series"],
         "idPrefixes": ["juku-"]},
    ],
    "catalogs": [
        {"type": "movie", "id": "juku-latest",
         "name": "Juku 最新", "extra": [{"name": "skip", "isRequired": False}]},
        {"type": "movie", "id": "juku-hot",
         "name": "Juku 热门", "extra": [{"name": "skip", "isRequired": False}]},
        {"type": "series", "id": "juku-series",
         "name": "Juku 剧集", "extra": [{"name": "skip", "isRequired": False}]},
        {"type": "movie", "id": "juku-search",
         "name": "Juku 搜索",
         "extra": [{"name": "search", "isRequired": True},
                   {"name": "skip", "isRequired": False}]},
    ],
    "idPrefixes": ["juku-"],
    "behaviorHints": {"configurable": False, "cinemetaFallback": False},
}

_C = None


def client():
    global _C
    if _C is None:
        _C = JukuClient()
    return _C


def _json(body, status=200):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def _meta_from_video(v):
    vid = v.get("id")
    return {
        "id": "juku-%s" % vid,
        "type": "series" if (v.get("typeId") == 2 or str(v.get("remark", "")).find("剧") >= 0) else "movie",
        "name": v.get("title") or v.get("originalName") or "未命名",
        "poster": v.get("pic") or "",
        "posterShape": "poster",
        "background": (v.get("backdropPath") or "").startswith("http") and v.get("backdropPath") or (
            "https://image.tmdb.org/t/p/w780" + v.get("backdropPath", "")) if v.get("backdropPath") else "",
        "description": v.get("content") or v.get("subTitle") or "",
        "releaseInfo": str(v.get("year") or ""),
        "director": v.get("director") or "",
        "cast": [c.strip() for c in (v.get("starring") or "").split(",") if c.strip()][:12],
        "genres": [t.get("name") for t in (v.get("tags") or []) if isinstance(t, dict)][:6],
    }


def handle_catalog(cat_id, extra):
    c = client()
    skip = int(extra.get("skip", "0") or "0")
    page = skip // 30 + 1
    try:
        if cat_id == "juku-search":
            q = extra.get("search", "")
            if not q:
                return _json({"metas": []})
            resp = c.search(q, page=page, page_size=30)
            items = resp.get("data", {}).get("items") or resp.get("data", {}).get("list") or resp.get("data", {}).get("records") or []
        elif cat_id == "juku-hot":
            resp = c.get_video_list(page=page, page_size=30, type_id=None)
            items = resp.get("data", {}).get("items") or resp.get("data", {}).get("list") or []
        elif cat_id == "juku-series":
            resp = c.get_video_list(page=page, page_size=30, type_id=2)
            items = resp.get("data", {}).get("items") or resp.get("data", {}).get("list") or []
        else:  # juku-latest
            resp = c.get_video_list(page=page, page_size=30)
            items = resp.get("data", {}).get("items") or resp.get("data", {}).get("list") or []
        metas = [_meta_from_video(v) for v in items if v.get("id")]
        return _json({"metas": metas})
    except Exception as e:
        return _json({"metas": [], "error": str(e)}, status=200)


def handle_meta(meta_id):
    vid = meta_id[len("juku-"):]
    try:
        c = client()
        det = c.get_video_detail(vid)
        v = det.get("data", {})
        m = _meta_from_video(v)
        # 剧集结构: 统计各线路集数
        pl = v.get("playListByLine", {})
        episodes = {}
        for line, lst in pl.items():
            for it in (lst or []):
                ep = it.get("episode")
                if ep:
                    episodes[ep] = True
        if episodes:
            m["videos"] = [{"id": "juku-%s:%d" % (vid, ep), "title": "第%d集" % ep,
                            "season": 1, "episode": ep} for ep in sorted(episodes)]
        return _json(m)
    except Exception as e:
        return _json({"id": meta_id, "type": "movie", "name": meta_id, "error": str(e)}, status=200)


def handle_stream(meta_id):
    """meta_id 形如 juku-<videoId> 或 juku-<videoId>:<ep> 。
    返回 playListByLine 中所有线路的真实 m3u8 (Episode 过滤可选)。"""
    vid = meta_id[len("juku-"):]
    ep = None
    if ":" in vid:
        vid, eps = vid.split(":", 1)
        try:
            ep = int(eps)
        except Exception:
            ep = None
    try:
        c = client()
        det = c.get_video_detail(vid)
        pl = det.get("data", {}).get("playListByLine", {})
        streams = []
        seen = set()
        for line, lst in pl.items():
            for it in (lst or []):
                if ep is not None and it.get("episode") != ep:
                    continue
                url = it.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                streams.append({
                    "url": url,
                    "title": "线路%s · %s" % (it.get("lineId", line), it.get("url", "").split("/")[2]),
                    "behaviorHints": {
                        "notWebReady": False,
                        "proxyHeaders": {"request": {"Referer": "https://api.jukuku.xyz/",
                                                     "User-Agent": "Mozilla/5.0"}},
                    },
                })
        if not streams:
            return _json({"streams": [{"title": "暂无免登录线路", "url": ""}]}, status=200)
        return _json({"streams": streams})
    except Exception as e:
        return _json({"streams": [{"title": "解析失败: %s" % str(e), "url": ""}]}, status=200)


def handler(event, context):
    try:
        path = event.get("path", "/")
        # 去掉函数名前缀 (如 /.netlify/functions/addon/...)
        path = path.split("/.netlify/functions/addon", 1)[-1] or "/"
        if path.startswith("/"):
            path = path[1:]
        parts = [p for p in path.split("/") if p]
        method = event.get("httpMethod", "GET")

        # query / extra
        qs = event.get("queryStringParameters") or {}
        # Stremio 把 extra 编码在路径里: catalog/movie/juku-latest/skip=30.json
        extra = dict(qs)
        if len(parts) >= 3 and parts[-1].endswith(".json"):
            leaf = parts[-1][:-len(".json")]
            if "=" in leaf:
                k, v = leaf.split("=", 1)
                extra[k] = v

        if not parts or parts[0] == "manifest.json":
            return _json(MANIFEST)
        if parts[0] == "catalog" and len(parts) >= 3:
            cat_id = parts[2].replace(".json", "")
            # Stremio 搜索目录把关键词放在路径第 4 段: .../juku-search/关键词.json
            if cat_id == "juku-search" and len(parts) >= 4:
                import urllib.parse as _up
                term = parts[3].replace(".json", "")
                extra["search"] = _up.unquote(term)
            return handle_catalog(cat_id, extra)
        if parts[0] == "meta" and len(parts) >= 3:
            meta_id = parts[2].replace(".json", "")
            return handle_meta(meta_id)
        if parts[0] == "stream" and len(parts) >= 3:
            meta_id = parts[2].replace(".json", "")
            return handle_stream(meta_id)
        return _json({"error": "not found", "path": path}, status=404)
    except Exception as e:
        return _json({"error": traceback.format_exc()}, status=500)
