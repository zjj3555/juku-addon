#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_site.py —— 用「实时剧库 API」生成静态 Stremio 插件文件。

产出 (写入本目录):
  manifest.json
  catalog/movie/juku-latest.json
  catalog/movie/juku-hot.json
  catalog/movie/juku-series.json
  stream/movie/juku-<id>.json        (每个视频一个，内含真实 HLS 直链)

这些文件可直接推到 gitee raw / GitHub Pages，SwitchStream 连公网 URL 即可播。
PC 不参与播放，仅在本脚本运行时(或 CI)生成一次。
"""
import os
import sys
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from juku_client import JukuClient

OUT = os.path.dirname(os.path.abspath(__file__))
PAGE = 40  # 每个片单取多少条

MANIFEST = {
    "id": "community.juku.hls",
    "version": "2.1.0",
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
        {"type": "movie", "id": "juku-hot", "name": "Juku 热门"},
        {"type": "series", "id": "juku-series", "name": "Juku 剧集"},
    ],
    "idPrefixes": ["juku-"],
    "behaviorHints": {"configurable": False, "cinemetaFallback": False},
}


def meta_from_video(v):
    return {
        "id": "juku-%s" % v.get("id"),
        "type": "movie",
        "name": v.get("title") or v.get("originalName") or "未命名",
        "poster": v.get("pic") or "",
        "posterShape": "poster",
        "background": v.get("backdropPath") or "",
        "description": v.get("content") or v.get("subTitle") or "",
        "releaseInfo": str(v.get("year") or ""),
        "director": v.get("director") or "",
        "cast": [c.strip() for c in (v.get("starring") or "").split(",") if c.strip()][:12],
    }


def build():
    c = JukuClient()
    os.makedirs(os.path.join(OUT, "catalog", "movie"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "stream", "movie"), exist_ok=True)

    # 1) 拉片单
    latest = c.get_video_list(page=1, page_size=PAGE).get("data", {}).get("items", [])
    series = c.get_video_list(page=1, page_size=PAGE, type_id=2).get("data", {}).get("items", [])
    try:
        hd = c.get_home().get("data", {})
        hot_raw = hd.get("movieVideos", []) or hd.get("hotVideos", []) or hd.get("recommendVideos", [])
    except Exception:
        hot_raw = []
    hot = hot_raw[:PAGE]

    buckets = {"juku-latest": latest, "juku-series": series, "juku-hot": hot}
    seen = set()
    for cat_id, items in buckets.items():
        metas = []
        for v in items:
            vid = v.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            # 1) 拉详情 -> 写 stream（只保留真有公开 HLS 的影片，避免列表里出现不能播的条目）
            streams = []
            spath = os.path.join(OUT, "stream", "movie", "juku-%s.json" % vid)
            try:
                det = c.get_video_detail(vid)
                pl = det.get("data", {}).get("playListByLine", {})
                used = set()
                for line, lst in pl.items():
                    for it in (lst or []):
                        url = it.get("url")
                        if not url or url in used:
                            continue
                        used.add(url)
                        streams.append({
                            "url": url,
                            "title": "线路%s · %s" % (it.get("lineId", line), url.split("/")[2]),
                            "behaviorHints": {"notWebReady": False},
                        })
                with open(spath, "w", encoding="utf-8") as f:
                    json.dump({"streams": streams}, f, ensure_ascii=False)
            except Exception as e:
                print("  ! detail fail %s: %s (keep previous if any)" % (vid, e))
                if os.path.exists(spath):
                    try:
                        prev = json.load(open(spath, encoding="utf-8")).get("streams", [])
                        if prev:
                            streams = prev
                    except Exception:
                        pass
            if streams:
                metas.append(meta_from_video(v))
            else:
                print("  - skip (no HLS) %s %s" % (vid, v.get("title")))
        with open(os.path.join(OUT, "catalog", "movie", "%s.json" % cat_id), "w", encoding="utf-8") as f:
            json.dump({"metas": metas}, f, ensure_ascii=False)
        print("catalog %s: %d playable metas" % (cat_id, len(metas)))

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False, indent=2)
    print("done. total videos:", len(seen))


if __name__ == "__main__":
    build()
