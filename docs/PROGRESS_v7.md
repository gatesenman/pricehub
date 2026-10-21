# v7（v1.5 团队协作）进度记录
时间均为北京时间（UTC+8）。

- [x] 阶段1 业务实现下沉 app/domains/*（821b996）
- [x] 阶段2-3 PostgreSQL COPY 写入、分页总数缓存/估算；/metrics 仅本机或令牌（3d29a19）
- [x] 阶段4a 多租户（SQLite 改写 / PG RLS）、账号、OIDC、邀请角色、配额（d7cbfc4）
- [x] 2026-10-21 22:05 阶段4b Redis Streams 队列 + 独立 worker（app/worker.py）+ 无 Redis 回退；模拟 OIDC；tests/test_v7_team.py 3 项通过（SQLite）；UI：登录页、工作区切换、团队与成员、用量页（v7.js）；podman 镜像 localhost/pricehub:v7 构建成功
- [x] 2026-10-21 22:08 UI e2e tests/test_v7_ui.py 通过，截图 screenshots/v7/01-07；compose 修复 YAML、增加 APP_PORT
- [x] 2026-10-21 22:10 PRD/ARCHITECTURE v7 章节更新（PDF 待压测后重导）
- [x] 2026-10-21 22:17 podman 构建镜像 localhost/pricehub:v7 成功；podman-compose 启动 db/redis/app（PostgreSQL 后端，/readyz ready）；worker 容器因 podman 无 systemd 健康检查一直 starting、podman-compose 卡住，改用 podman run 同网络启动，经容器 worker 完成采集任务（Redis 后端）；宿主访问 /metrics 无令牌 401
- [x] 2026-10-21 22:30 修复：RLS 表不允许 COPY → 改为 COPY 到临时表再 INSERT…SELECT；SQLite 启动执行 PRAGMA optimize 避免误用 workspace_id 索引（同款组比价 15ms 回退）。PG 压测完成（COPY 11 万点/秒，机器负载约 100，数字偏保守）

- [x] 2026-10-21 23:51 SQLite 压测完成；修复多租户下分页退化（索引改为 products(workspace_id,id)，345ms→4.6ms）；BENCHMARK.md 追加 v7 复测
