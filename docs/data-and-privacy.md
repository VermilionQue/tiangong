# 数据、权限与备份

## 本地保存与家庭共享

数据根目录由 `TIANGONG_STATE_DIR` 指定。管理员记录在根目录，其他账号记录在 `users/<账号标识>/`。家庭厨房记录保存在根目录的 `recipes.sqlite3`，由厨房成员关系控制访问。登录配置位于 `security/`。

健康附件、账单、导入原件和照片保存在这个私有数据目录内；源码随附的 `data/` 只有基础菜谱与公开营养数据。

家庭厨房可看到成员主动填写的忌口和偏好。关联自己的健康账号并允许共享后，菜谱模块读取有限健康摘要用于饮食建议；原始报告附件不随之共享。不要把家庭共用账号当作个人隐私边界。

## 外部请求

普通查询和统计在本地进行。天气、游戏资料等联网功能在使用时请求对应服务；具体连接由部署者配置。主动请求山君分析时，会将用于本次分析的餐单、忌口与经授权共享的健康摘要交给配置的 OpenClaw 智能体；智能体背后的模型提供方及其保存策略由部署者选择。

浏览器不缓存私人 API 响应。添加主屏幕不是离线数据库，也不会在离线后自动排队提交修改。

## 备份与恢复

服务会请求增量快照备份；默认目标为数据根目录中的 `backups/`。这能用于版本恢复，但与原数据处于同一设备，不能抵御整机损坏。建议把备份目标设置到另一个受控存储位置。

```sh
export TIANGONG_BACKUP_DIR=/mnt/family-backup/tiangong
# 使用挂载盘时同时指定真实挂载点，断开后将拒绝备份，避免写入假挂载目录。
export TIANGONG_BACKUP_MOUNT=/mnt/family-backup
python scripts/nas_backup.py --help
```

备份程序使用 SQLite 在线备份，覆盖管理员与成员数据，并保留文件校验信息。权限报告区分“请求了私有权限”与“文件系统确实兑现权限”；NAS 共享 ACL 仍需部署者设置。

运行 `python scripts/nas_backup.py` 创建快照，输出包含快照目录。用实际路径替换下面的示例，先验证整个快照能恢复并通过数据库完整性检查：

```sh
python scripts/nas_backup.py --verify /path/to/backup/snapshots/SNAPSHOT
```

需要取出恢复文件时，停止后端、网关和提醒等所有写入服务，保持原来的备份环境变量，在仓库根目录运行：

```sh
python - /path/to/backup/snapshots/SNAPSHOT /path/to/empty-restore-directory <<'PY'
import sys
sys.path.insert(0, 'scripts')
from nas_backup import restore_to_empty
manifest = restore_to_empty(sys.argv[1], sys.argv[2])
print('已恢复并校验', len(manifest['files']), '个文件')
PY
```

目标必须为空，并且不能位于当前数据目录或备份目录之内，也不能包含它们。核对恢复结果后，再指定恢复目录作为新的数据目录；保留原数据副本。

快照不包含所有凭据和运维配置。`security/gateway.json`、`security/upstream-secret` 以及外部集成凭据需要另行安全备份，并在切换数据目录前恢复到相应位置，不能上传源码仓库。切勿直接覆盖唯一数据源。

健康信息、账单、家庭关系与快照都应使用合适的文件系统权限和磁盘保护。报告问题时用合成样例复现，不要把真实数据库或附件放入公开 Issue。
