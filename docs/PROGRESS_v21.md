# PriceHub v21：Windows 回退与服务进程安全评分

时间：2026-11-24（Asia/Shanghai，UTC+8）

## 审计依据与实现
审计发现稀疏/回调相似度在 fork worker 中被当作 0，且 ASGI 工作线程 fork 存在继承锁死锁风险。仅单线程 CLI 的 dense 评分允许 fork；Windows/spawn、稀疏、服务多线程统一正确串行回退；清理池共享状态、顺序收集结果，失败有日志。

## 验证
test_v21_portable_scores + test_v16_dedup_scale：8 passed；模拟仅 spawn、线程内禁 fork、20301 对稀疏回调与串行一致。未在 Windows 真机执行，未重新声称 1M 性能。

## 边界
仅临时数据库和目录；未运行真实外发、未改生产数据；不代表真实业务数据覆盖。
