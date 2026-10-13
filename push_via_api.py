#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub Contents API 把 addon 文件推到仓库（替代 git，绕过 curl 被 reset 的问题）。
用法: GH=token python push_via_api.py
"""
import os, sys, json, base64, urllib.request

TOKEN = os.environ.get("GH") or sys.argv[1]
REPO = "zjj3555/juku-addon"
BASE = os.path.dirname(os.path.abspath(__file__))
AUTH = "Bearer " + TOKEN
EXCLUDE = {".git", "__pycache__", ".gitignore", "JUKU_DEVICE_PRI.b64.txt", "build_site.py", "juku_client.py", "README.md"}


def api(path, method="GET", data=None):
    url = "https://api.github.com/repos/%s/contents/%s" % (REPO, path)
    headers = {"Authorization": AUTH, "Accept": "application/vnd.github+json",
               "Content-Type": "application/json", "User-Agent": "juku-push"}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def walk():
    out = []
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in EXCLUDE]
        for f in files:
            if f in EXCLUDE or f.endswith(".pyc"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, BASE).replace("\\", "/")
            out.append((rel, full))
    return out


def main():
    files = walk()
    # order: put smaller config files first
    files.sort(key=lambda x: x[0])
    for rel, full in files:
        with open(full, "rb") as fh:
            content = base64.b64encode(fh.read()).decode()
        # check existing sha
        st, existing = api(rel)
        sha = existing.get("sha") if st == 200 else None
        msg = ("add " if not sha else "update ") + rel
        data = {"message": msg, "content": content, "branch": "main"}
        if sha:
            data["sha"] = sha
        code, resp = api(rel, "PUT", data)
        if code in (200, 201):
            print("OK  ", rel)
        else:
            print("FAIL", rel, code, str(resp)[:200])
    # enable GitHub Pages (branch main, / root)
    url = "https://api.github.com/repos/%s/pages" % REPO
    headers = {"Authorization": AUTH, "Accept": "application/vnd.github+json",
               "Content-Type": "application/json", "User-Agent": "juku-push"}
    body = json.dumps({"source": {"branch": "main", "path": "/"}}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=30)
        print("PAGES enabled:", r.status)
    except urllib.error.HTTPError as e:
        print("PAGES (maybe already enabled):", e.code, e.read().decode()[:150])


if __name__ == "__main__":
    main()
