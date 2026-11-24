# PriceHub v25：采集写入并发与提交后副作用

时间：2026-11-24（Asia/Shanghai，UTC+8）

## 审计依据与实现
同 external_id 并发读后插存在竞态，且 seller-only 更新会清空 attrs。upsert/record_price 使用事务，SQLite BEGIN IMMEDIATE 覆盖主商品/规格/价格；仅更新提供的元数据。新增 after_commit：事务失败不发价格事件、不发布半成品索引，savepoint 回滚丢弃对应回调。

## 验证
test_v25_collect_atomic + test_v22_transactions + test_v23_price_history：12 passed；6 线程 12 次同 SKU 写入仅 1 商品和 12 历史；坏规格导致全部回滚且事件计数 0。

## 边界
仅临时数据库和目录；未运行真实外发、未改生产数据；不代表真实业务数据覆盖。
