# 部署完成记录 (2026-10-13)

## 公网插件地址（SwitchStream / nxmp / Stremio 安装用）
```
https://zjj3555.github.io/juku-addon/manifest.json
```

## 验证结果
- manifest.json：HTTP 200，id=community.juku.hls v2.1.0，catalogs=juku-latest/juku-hot/juku-series
- catalog：每个 40 部（共 120），含真实片名/海报
- stream：每部 5~13 条真实 HLS 直链（playListByLine 公开 CDN）
- 抽样 m3u8（fengbao13.com）：HTTP 200，含 .ts 分片 —— 可播

## 满足的硬约束
- PC 不是播放服务器：解析在生成时完成（本机跑 build_site.py 或 GitHub Actions），产物纯静态 JSON 托管在 GitHub Pages。Switch 直连公网，PC 可关机。
- 真实片源 = api.jukuku.xyz 的 playListByLine 公开 CDN，免登录。

## 仓库
- https://github.com/zjj3555/juku-addon （public, GitHub Pages 已开）
- 已配置 Secrets：JUKU_DEVICE_PRI（device.pri 的 base64，用于 CI 重新生成）
- 注意：CI 自动刷新 workflow（.github/workflows/refresh.yml）因 GitHub API 限制无法经 API 写入 .github/ 目录，需手动在网页端添加，或直接本机跑 build_site.py 后 git push。

## 本地重新生成（如需更新片单/线路）
```
pip install cryptography
python build_site.py        # 读 ~/.mvmultiplatform/device.pri 或 JUKU_DEVICE_PRI 环境变量
git add catalog stream manifest.json && git commit -m up && git push
```
（本仓库在 D:\Downloads\PCB\juku-deploy-repo，但 push 用 API；如要 git push 需在本机配 token/SSH）

## 隐私铁律
- device.pri / JUKU_DEVICE_PRI / real_list.json 永不提交明文。
- 已设 .gitignore 屏蔽 *.pri、JUKU_DEVICE_PRI*。
