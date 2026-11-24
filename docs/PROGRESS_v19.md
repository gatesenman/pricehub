# PriceHub v19：单文件原子索引快照

时间：2026-11-24（Asia/Shanghai，UTC+8）

## 审计依据与实现
v15 多文件分别 replace 允许读到不同代的 ids/index；现改为单个 NPZ 快照、一次 fsync+replace 发布，内含 JSON/ids/向量/序列化 FAISS 与 SHA256；严格校验 shape/dtype/条数/维度/有限值，损坏回退重建。v1 缓存失效，不迁移商品数据。

## 验证
test_v19_snapshots + test_v15_faiss_persist：13 passed；包含并发 20 次发布/读取、注入 replace 失败保留旧代、篡改 payload 校验。

## 边界
仅临时数据库和目录；未运行真实外发、未改生产数据；不代表真实业务数据覆盖。
