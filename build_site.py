#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_site.py —— 用「实时剧库 API」生成静态 Stremio 插件文件。

产出 (写入本目录):
  manifest.json
  catalog/movie/juku-latest.json / juku-hot.json / juku-series.json
  stream/movie/juku-<id>.json   (每个视频一个，内含真实 HLS 直链)
  posters/juku-<id>.<ext>        (海报，自托管到 Pages，规避外链被拦截)

设计要点:
  - 流地址直接用剧库原始 m3u8 URL。Switch 端 SwitchStream 能直接访问这些 CDN，
    PC 不需要做代理或重写(本机也无法访问这些 CDN)。
  - behaviorHints.notWebReady=true: 这是“直链 m3u8 文件”的正确做法，
    让 Stremio 走原生播放器，避免 webview 误播导致的 2 秒报错。
  - 海报自托管到 Pages: img.jukuku.xyz 在 Switch 上被拦截(会回退成默认图→功夫女足)，
    自托管后全部同域，必然显示。
  - 详情有缓存(_cache/detail), 重跑极快; 海报已存在则跳过(续传)。
"""
import os
import sys
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from juku_client import JukuClient

OUT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(OUT, "_cache")
PAGE = 40
PAGES_BASE = "https://zjj3555.github.io/juku-addon"
UA = {"User-Agent": "Mozilla/5.0"}
WORKERS = 8
REQ_TIMEOUT = 20

MANIFEST = {
    "id": "community.juku.hls",
    "version": "2.2.0",
    "name": "Juku 剧库 (实时)",
    "description": "实时解析剧库 jukuku.xyz 的免登录 HLS 直链，SwitchStream / nxmp / Stremio 通用。公网托管，PC 无需常开。",
    "types": ["movie"],
    "resources": [
        {"name": "catalog", "types": ["movie"], "idPrefixes": ["juku-"]},
        {"name": "meta", "types": ["movie"], "idPrefixes": ["juku-"]},
        {"name": "stream", "types": ["movie"], "idPrefixes": ["juku-"]},
    ],
    "catalogs": [
        {"type": "movie", "id": "juku-latest", "name": "Juku 最新"},
        {"type": "movie", "id": "juku-series", "name": "Juku 剧集"},
        {"type": "movie", "id": "juku-hot", "name": "Juku 热门"},
    ],
    "idPrefixes": ["juku-"],
    "behaviorHints": {"configurable": False, "cinemetaFallback": False},
}


def http_text(url, timeout=REQ_TIMEOUT):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def save_bytes(rel, data):
    p = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def download_poster(vid, url):
    if not url:
        return ""
    rel = "posters/juku-%s" % vid
    p = os.path.join(OUT, rel)
    for ext in ("jpg", "png", "webp"):
        if os.path.exists(p + "." + ext):
            return rel + "." + ext
    try:
        data = urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=20).read()
        if len(data) < 500:
            return ""
        low = url.lower()
        if low.endswith(".webp"):
            ext = "webp"
        elif low.endswith(".png"):
            ext = "png"
        else:
            ext = "jpg"
        save_bytes(rel + "." + ext, data)
        return rel + "." + ext
    except Exception as e:
        print("  ! poster fail %s: %s" % (vid, e))
        return ""


def cache_path(vid):
    return os.path.join(CACHE, "detail", "%s.json" % vid)


def get_detail(c, vid):
    cp = cache_path(vid)
    if os.path.exists(cp):
        try:
            return json.load(open(cp, encoding="utf-8"))
        except Exception:
            pass
    det = retry(lambda: c.get_video_detail(vid))
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    json.dump(det, open(cp, "w", encoding="utf-8"))
    return det


def meta_from_video(v, poster_rel):
    poster = (PAGES_BASE + "/" + poster_rel) if poster_rel else ""
    return {
        "id": "juku-%s" % v.get("id"),
        "type": "movie",
        "name": v.get("title") or v.get("originalName") or "未命名",
        "poster": poster,
        "posterShape": "poster",
        "background": "",
        "description": v.get("content") or v.get("subTitle") or "",
        "releaseInfo": str(v.get("year") or ""),
        "director": v.get("director") or "",
        "cast": [c.strip() for c in (v.get("starring") or "").split(",") if c.strip()][:12],
    }


def retry(fn, tries=5, wait=2.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print("  ! retry %d/%d: %s" % (i + 1, tries, e))
            time.sleep(wait * (i + 1))
    raise last


def build():
    t0 = time.time()
    c = JukuClient()
    c.ensure_session()
    os.makedirs(os.path.join(OUT, "catalog", "movie"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "stream", "movie"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "posters"), exist_ok=True)

    latest = retry(lambda: c.get_video_list(page=1, page_size=PAGE).get("data", {}).get("items", []))
    series = retry(lambda: c.get_video_list(page=1, page_size=PAGE, type_id=2).get("data", {}).get("items", []))
    try:
        hd = retry(lambda: c.get_home().get("data", {}))
        hot_raw = hd.get("movieVideos", []) or hd.get("hotVideos", []) or hd.get("recommendVideos", [])
    except Exception as e:
        print("  ! home fail: %s (hot catalog may be empty)" % e)
        hot_raw = []
    hot = hot_raw[:PAGE]

    buckets = {"juku-latest": latest, "juku-series": series, "juku-hot": hot}
    order = []
    seen = set()
    for cat_id, items in buckets.items():
        for v in items:
            vid = v.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            order.append((cat_id, v))

    print("== fetching %d details (parallel) ==" % len(order))
    details = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut = {ex.submit(get_detail, c, v["id"]): (cid, v) for cid, v in order}
        for f in as_completed(fut):
            cid, v = fut[f]
            try:
                det = f.result()
            except Exception as e:
                print("  ! detail fail %s: %s" % (v.get("id"), e))
                continue
            details[v["id"]] = (v, det)

    video_streams = {}
    video_meta = {}
    poster_tasks = []
    for cid, v in order:
        vid = v["id"]
        if vid not in details:
            continue
        vv, det = details[vid]
        pl = det.get("data", {}).get("playListByLine", {})
        streams = []
        used = set()
        for line, lst in pl.items():
            for it in (lst or []):
                url = it.get("url")
                if not url or url in used:
                    continue
                used.add(url)
                line_id = it.get("lineId", line)
                streams.append((vid, line_id, url))
        if streams:
            video_streams[vid] = streams
            video_meta[vid] = vv
            poster_tasks.append((vid, vv.get("pic")))

    print("== fetching %d posters (parallel) ==" % len(poster_tasks))
    poster_map = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        fut = {ex.submit(download_poster, vid, url): vid for vid, url in poster_tasks}
        for f in as_completed(fut):
            poster_map[fut[f]] = f.result()

    cat_metas = {"juku-latest": [], "juku-series": [], "juku-hot": []}
    for cid, v in order:
        vid = v["id"]
        if vid not in video_meta:
            continue
        poster_rel = poster_map.get(vid, "")
        cat_metas.setdefault(cid, []).append(meta_from_video(video_meta[vid], poster_rel))
        streams_out = []
        for (v2, line, url) in video_streams[vid]:
            host = url.split("/")[2] if "//" in url else url
            streams_out.append({
                "url": url,
                "title": "线路%s · %s" % (line, host),
                "behaviorHints": {"notWebReady": True},
            })
        spath = os.path.join(OUT, "stream", "movie", "juku-%s.json" % vid)
        json.dump({"streams": streams_out}, open(spath, "w", encoding="utf-8"), ensure_ascii=False)

    for cid, metas in cat_metas.items():
        with open(os.path.join(OUT, "catalog", "movie", "%s.json" % cid), "w", encoding="utf-8") as f:
            json.dump({"metas": metas}, f, ensure_ascii=False)
        print("catalog %s: %d playable metas" % (cid, len(metas)))

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False, indent=2)
    print("done. videos=%d posters=%d  (%.1fs)" % (
        len(video_meta), len([p for p in poster_map.values() if p]), time.time() - t0))


if __name__ == "__main__":
    build()
