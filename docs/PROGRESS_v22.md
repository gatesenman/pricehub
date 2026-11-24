# PriceHub v22：合并及撤销原子事务

时间：2026-11-24（Asia/Shanghai，UTC+8）

## 审计依据与实现
合并逐条 commit，故障会留下已搬价格但没有审计记录。新增线程本地事务/嵌套 savepoint，SQLite BEGIN IMMEDIATE 串行读改写，merge/unmerge 全部同一提交；缺失商品明确 404。没有 schema 迁移，不需要修改生产库或备份迁移。

## 验证
test_v22_transactions + test_v12_dedup：7 passed；注入审计写入故障验证价格/商品/审计全部回滚，嵌套回滚和合并撤销往返通过。PostgreSQL 分支未在真实服务器验证。

## 边界
仅临时数据库和目录；未运行真实外发、未改生产数据；不代表真实业务数据覆盖。
