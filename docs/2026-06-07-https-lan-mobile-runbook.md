# 局域网 HTTPS：让手机访问并发语音 / 图片 / 文件

2026-06-07。给手机经同 Wi-Fi 访问本机 Soundspeed admin，能录语音 note、传图片/文件。

## 为什么要 HTTPS

只有**语音**逼着上 HTTPS。手机录音走 `getUserMedia`（`useVoiceRecorder.ts`、`useMicLevel.ts`），
浏览器只在 secure context（HTTPS 或 localhost）下放行麦克风；手机用局域网 IP 走 HTTP 不是 secure
context，麦克风直接被禁。图片/文件走普通 `<input type=file>`，HTTP 就能传，HTTPS 只是顺带覆盖。

连锁约束：前端页面一旦 HTTPS，就不能再发 HTTP / `ws://`（mixed content 被拦）。前端 WS 从
`VITE_API_BASE` 派生（`config.ts`：http→ws / https→wss），所以**后端也得在 HTTPS 后面**。

方案：Caddy 占 443 做唯一 HTTPS 终止点，前后端两个 HTTP 服务不动，同源转发。前后端代码零改动，
唯一新增的是构建期配置 `frontend/.env.production`（gitignore，本机用）。

## 一次性安装（Mac）

```bash
brew install mkcert caddy
mkcert -install                      # 本地 CA 装进 Mac 钥匙串（可能弹一次授权）

# 从仓库根目录，证书生成到 ./certs（已 gitignore）
mkdir -p certs
mkcert -cert-file certs/soundspeed.pem -key-file certs/soundspeed-key.pem \
  macbook-pro-1370.local 192.168.0.190 localhost 127.0.0.1
```

SAN 同时含 `.local` 主机名和局域网 IP，一张证书覆盖两种访问方式。`.local`（mDNS）比裸 IP 抗
DHCP 漂移，优先用主机名访问。

## 每次启动

```bash
# 1. 前端产物（给手机用，必须是 build 不是 dev）
cd frontend && pnpm build          # 读 .env.production 的 HTTPS host，出 frontend/dist
cd ..

# 2. 后端照常起，监听 0.0.0.0:8000，dev 模式 token 固定 devtoken
PORT=8000 SOUNDSPEED_DEV=1 python -m backend.api

# 3. Caddy（443 是特权端口，要 sudo）
sudo caddy run --config ./Caddyfile

# 4. 手机同 Wi-Fi 开 https://macbook-pro-1370.local/admin
```

**别让手机连 `pnpm dev`**：那是 PR #55 白屏 OOM 的元凶（React19 dev 的 PerformanceMeasure 泄漏
×60fps 重渲），现场跑 dev 直接崩标签页。手机只连 build 产物。

## 手机信任 mkcert 根 CA（不做则 wss 握手失败）

`wss://` 在手机 Safari 上没有「仍要访问」的旁路，证书不受信直接连不上。根 CA 文件：

```bash
open "$(mkcert -CAROOT)"           # 里面的 rootCA.pem 传到手机
```

**iOS**：AirDrop / 邮件 `rootCA.pem` 到 iPhone → 点开 → 设置 > 通用 > VPN与设备管理 里装这个
描述文件 → **还要**去 设置 > 通用 > 关于本机 > 证书信任设置，把 mkcert 那条打开「完全信任」。
第二步最容易漏，漏了照样连不上。

**Android**：传 `rootCA.pem` 到手机 → 设置 > 安全 > 加密与凭据 > 安装证书 > CA 证书。

## 两个会卡住的坑

1. **手机要手填 token**。生产 build 不自动填 devtoken（只 dev 构建自动填），手机进 /admin 后在
   「服务器连接」里手填 `devtoken` 才连得上 WS，否则 401。
2. **统一用 `.local` 主机名访问**，别用 IP。`VITE_API_BASE` 钉死了 hostname，混用 IP 会变跨源
   （CORS 是 `*` 能跑通，但容易自己绕晕）。

## 换机器

改 `Caddyfile` 站点地址和 `.env.production` 的 hostname/IP，按新 SAN 重新生成 `./certs`。
