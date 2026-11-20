# 自行部署

## 运行环境

当前部署目标是 macOS 或 Linux，Python 3.11+。部分服务使用 POSIX 文件锁与文件权限；尚未验证 Windows 原生运行。Node.js 20+ 与 npm 只用于构建。Python 图像处理和表格导入依赖见 `requirements.txt`。

先按 README 创建虚拟环境、安装依赖并运行 `npm run build`。仓库包含构建后的资源，但部署前重新构建可验证源码与资源清单一致。

## 一个数据根目录

所有服务必须使用同一组环境变量。默认数据目录为 `~/.local/share/tiangong`。也可设置 `TIANGONG_STATE_DIR` 为本机非同步目录或 NAS 上由唯一服务实例管理的目录。

`.env.example` 仅为配置说明；程序不会自动读取 `.env`。在启动服务的 shell、systemd 或其他进程管理器中显式配置变量。

```sh
export TIANGONG_STATE_DIR="$HOME/.local/share/tiangong"
export TIANGONG_ADMIN_USERNAME=admin
```

管理员用户名应在首次初始化前设置，初始化后不要只改环境变量来重命名账号。请保管整个数据目录及其权限。

## 仅在当前电脑体验

```sh
python scripts/work_server.py serve
```

访问 `http://127.0.0.1:8765`。没有登录网关配置时，这个回环地址用于单机体验。不要将该端口直接映射到公网。若已配置登录网关，后端会要求服务间认证，应通过 HTTPS 网关访问。

## 家庭成员登录与远程访问

准备自己控制的域名，按实际域名替换下面的 `journal.example.com`。网关要求 HTTPS origin。

**先初始化网关，再启动后端**，保证后端加载服务间认证配置：

```sh
python scripts/public_gateway.py configure --origin https://journal.example.com
python scripts/public_gateway.py set-password
```

密码交互输入，至少 14 个字符；不要将密码写入命令行或提交仓库。随后在两个进程中启动（均需相同的虚拟环境与环境变量）：

```sh
python scripts/work_server.py serve
```

```sh
python scripts/public_gateway.py serve
```

后端监听 `127.0.0.1:8765`，认证网关监听 `127.0.0.1:8770`。在网关前配置 HTTPS 反向代理，例如 Caddy：

```caddyfile
journal.example.com {
    reverse_proxy 127.0.0.1:8770
}
```

对外只开放 HTTPS 反向代理。保持网关 origin 与浏览器使用的域名一致，不要把后端端口设为代理目标。默认不信任外来代理身份头；Cloudflare Tunnel 属于可选运维方案，不是必需依赖。

登录后从账号管理添加家庭成员，成员通过自己的账号使用。加入同一个家庭厨房后共享菜谱、点菜和餐单；私人笔记、账本、健康报告等按账号隔离。家庭厨房中的忌口、饮食目标与主动共享的健康摘要对该厨房成员可见。

上述命令和 Caddy 配置是启动示例；本候选版已验证本机服务与模拟 HTTPS 网关，尚未验证每一种 NAS、域名服务或外部代理组合。

## 更新与停止

先备份，再停止后端和网关，更新源码、依赖，构建并运行测试，最后用相同数据目录启动。数据库可能在首次调用对应模块时执行结构迁移；回退旧代码前需准备迁移前备份。

服务长期运行可交给操作系统进程管理器。不要重复启动多个实例对同一个数据目录写入，也不要同时启用多个提醒调度。
