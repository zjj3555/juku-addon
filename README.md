# Juku 剧库 Stremio 插件（公网托管版）

把 **剧库 jukuku.xyz** 的免登录 HLS 直链做成 Stremio 插件，供 **SwitchStream / nxmp / 任意 Stremio 客户端** 使用。

**关键**：解析在「生成时」完成（本机跑 `build_site.py` 或 GitHub Actions 每 6 小时自动跑），
产物是纯静态 JSON，托管在 **GitHub Pages**。播放时 Switch 直接连公网，**PC 无需常开**。

## SwitchStream 安装地址（GitHub Pages）
```
https://<你的用户名>.github.io/<仓库名>/manifest.json
```
例如：`https://zjj3555.github.io/juku-addon/manifest.json`

## 两种模式
1. **静态版（本仓库根目录）** —— 已生成 `catalog/` `stream/` `manifest.json`，直连即可。已含 120 部最新/热门/剧集，每部 5~13 条真实 HLS 线路。
2. **真·动态版（`netlify/` 子目录）** —— 部署到 Netlify 后按需实时解析任意视频，支持搜索；需配置环境变量 `JUKU_DEVICE_PRI`（device.pri 的 base64）。

## 本机重新生成静态版
```bash
pip install cryptography
python build_site.py          # 用本地 ~/.mvmultiplatform/device.pri 或 JUKU_DEVICE_PRI 环境变量
```
生成后把 `catalog/ stream/ manifest.json` 提交推送，GitHub Pages 自动更新。

## 自动刷新（无需 PC 在线）
仓库已配置 `.github/workflows/refresh.yml`：每 6 小时自动重新生成并推送。
需在仓库 **Settings → Secrets → Actions** 添加 `JUKU_DEVICE_PRI`（device.pri 的 base64）。

## 隐私铁律
- `device.pri` 等同你的设备身份，**绝对不要**明文提交。只存为 Secret 或本地文件。
- 不要提交 `real_list.json` 之类含个人收藏的私有数据。

## 来源说明
- 真实播放源：`api.jukuku.xyz` 的 `playListByLine` 公开 CDN（fengbao13.com / svip.ryiplay18.com 等），无需登录即可播。
- `panList`（网盘）需登录，未使用。
