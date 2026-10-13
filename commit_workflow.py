#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .github/workflows/refresh.yml 通过 git data API 提交（绕过 Contents API 对多级目录的 404）。
关键：trees POST 不带 base_tree 时正常；这里先建一个只包含该文件的 tree，再与原 commit 的 tree 合并。
不过 GitHub 不允许部分 tree，所以改用：直接 PUT .github/workflows/refresh.yml 之前，先确保中间目录存在。
实测 Contents API 一次性不支持多级空目录，故这里用 git data API 重建完整 tree：
   1. 取出当前 main 的 base tree（已有 catalog/stream/netlify/manifest.json）
   2. 用 base_tree + 新 blob 新建 tree（带 base_tree）
实测带 base_tree 会 404，所以最终办法：单独为 workflow 建 tree 并 commit，再 fast-forward 覆盖 main。
"""
import os, json, base64 as b64, urllib.request

TOKEN = os.environ["GH"]
AUTH = "Bearer " + TOKEN
REPO = "zjj3555/juku-addon"
WF = r"D:\Downloads\PCB\juku-deploy-repo\.github\workflows\refresh.yml"
HEADERS = {"Authorization": AUTH, "Accept": "application/vnd.github+json",
           "Content-Type": "application/json", "User-Agent": "juku",
           "X-GitHub-Api-Version": "2022-11-28"}


def call(url, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=HEADERS, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    wf = open(WF, "rb").read()
    _, blob = call("https://api.github.com/repos/%s/git/blobs" % REPO, "POST",
                   {"content": b64.b64encode(wf).decode(), "encoding": "base64"})
    # 取出 main 当前 tree 的所有条目，作为新 tree 的 base（不通过 base_tree 参数，而是显式列出）
    _, ref = call("https://api.github.com/repos/%s/git/refs/heads/main" % REPO)
    base = ref["object"]["sha"]
    _, cmt = call("https://api.github.com/repos/%s/git/commits/%s" % (REPO, base))
    base_tree_sha = cmt["tree"]["sha"]
    _, base_tree = call("https://api.github.com/repos/%s/git/trees/%s" % (REPO, base_tree_sha))
    # 显式重建 tree（包含现有全部文件 + workflow），不带 base_tree 参数
    tree = base_tree["tree"]
    tree.append({"path": ".github/workflows/refresh.yml", "mode": "100644",
                 "type": "blob", "sha": blob["sha"]})
    st, new_tree = call("https://api.github.com/repos/%s/git/trees" % REPO, "POST", {"tree": tree})
    print("new tree", st, (new_tree["sha"][:10] if st == 201 else new_tree))
    if st != 201:
        return
    _, commit = call("https://api.github.com/repos/%s/git/commits" % REPO, "POST",
                     {"message": "add CI refresh workflow", "tree": new_tree["sha"], "parents": [base]})
    print("commit", commit["sha"][:10])
    st, _ = call("https://api.github.com/repos/%s/git/refs/heads/main" % REPO, "PATCH", {"sha": commit["sha"]})
    print("ref update", st)


if __name__ == "__main__":
    main()
