# Openworld VPS Auto Renew

基于 Playwright 的 Openworld VPS 自动续期。

## 部署

### 1. Fork 或新建仓库

把 `apprenew.py`、`requirements.txt`、`.github/workflows/renew.yml` 放进去。

### 2. 配置 Secrets

仓库 → Settings → Secrets and variables → Actions → New repository secret：

| 名称 | 必填 | 说明 |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Discord 账号 Token |
| `DISCORD_GUILD_ID` | 可选 | 默认 `1525632757072658502` |
| `TG_BOT_TOKEN` | 可选 | Telegram Bot Token |
| `TG_CHAT_ID` | 可选 | Telegram Chat ID |
| `ACCOUNT_NAME` | 可选 | 通知里显示的名字 |

### 3. 手动触发一次

Actions → Auto Renew Openworld VPS → Run workflow

### 4. 定时

默认每天 UTC 02:00（北京 10:00）跑一次，可在 `renew.yml` 里改 cron。

## 查看日志 / 截图

- 运行日志：Actions → 具体 run
- 截图：run 页面底部 **Artifacts** → `screenshots-N`
- 保留 7 天

## 安全提醒

- **不要**把 Token 写进代码或 commit
- Token 泄漏后立即去 Discord 改密
- Secrets 只在 Actions 运行时注入，日志会自动打码

## 免责声明

仅供学习。使用本工具可能违反服务条款，风险自负。
