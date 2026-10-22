# v10（Prophet 回测 + 分布式采集节点）进度
时间均为北京时间（UTC+8）。

- [x] Prophet 加入回测：`backtest_with_prophet`，快速引擎（维持现价 / baseline / STL+ARIMA）同步返回；
      Prophet 在后台线程逐窗口拟合，结果按“历史条数 + 最后一条价格”缓存，有新价格自动失效。
      接口返回 `prophet.status`（running / done / failed / unavailable），`?sync=true` 可同步计算。界面每 2 秒轮询，完成后自动并入对比表。
- [x] 分布式采集节点（Redis Streams）：
      - 注册与心跳：每个消费进程写 `node:<主机-进程号>`（TTL = 3 倍心跳，最少 10 秒），并保留最后快照以显示离线节点；
      - 任务租约：执行中任务写 `lease_until`，心跳时用 XCLAIM 续租；节点宕机后，存活节点用 XAUTOCLAIM 认领空闲超过租约时长的任务重跑（记录 reclaimed_from 与计数）；
      - 排空 / 恢复：`POST /api/nodes/{id}/drain|resume`，排空的节点不再领取新任务；离线节点可 `forget`；
      - 探测任务：`POST /api/nodes/probe` 派发 node.probe，用来验证分发；
      - 内存队列回退：只显示本进程一个内嵌节点，并提示如何开启多节点。
      - 配置：`PRICEHUB_HEARTBEAT_S`（默认 5）、`PRICEHUB_LEASE_S`（默认 30）。
- [x] 设置 → 采集节点 页面（v10.js）：节点表（状态、并发、执行中、完成、失败、接管、最后心跳、本次探测分布），每 5 秒自动刷新。
- [x] 端到端：tests/test_v10_nodes.py 启动 API + 2 个独立 worker 进程，验证注册、心跳、任务分到两个节点、排空/恢复、kill -9 一个节点后任务被另一节点接管、离线显示与移除、内存回退、Prophet 异步+缓存；tests/test_v10_ui.py 界面与截图 screenshots/v10/。
- [ ] TimescaleDB：本机 apt 源里没有 timescaledb 包，需要额外添加第三方软件源，本轮跳过。
- 仍未做：真实官方 API 签名与联调（需密钥）、TimescaleDB/ClickHouse、移动端、pgvector 扩展安装；节点目前不支持按地域/标签定向派发。
