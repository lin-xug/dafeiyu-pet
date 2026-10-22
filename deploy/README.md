# 部署用 unit 文件

这两份是**模板**，直接复制用不了，先把占位符改掉：

| 占位符 | 换成 |
|---|---|
| `/path/to/dafeiyu` | 本项目所在目录，例如 `/home/you/code/dafeiyu` |
| `/path/to/Napcat` | NapCat 安装目录（里面有 `opt/QQ/qq`） |
| `YOUR_QQ` | 机器人要登录的 QQ 号 |

```bash
mkdir -p ~/.config/systemd/user
cp deploy/*.service ~/.config/systemd/user/
# 改完之后：
systemctl --user daemon-reload
systemctl --user enable --now napcat dafeiyu-bot
loginctl enable-linger $USER      # 没登录图形界面时也能跑
```

`dafeiyu-bot.service` 里写了 `Wants=napcat.service`，所以启动机器人时会捎带拉起 NapCat；
反过来 NapCat 重启不会带动机器人（OneBot 断线由机器人自己重试上报，不需要重启）。
