# v18–v27 自主迭代状态

基线：bf2b4ca；本地 checkout 按要求实施。现有日志/截图脏文件保持原状。

审计发现：工作区缓存键缺失；多文件索引非原子发布；并行分支在无 dense 相似度时语义错误；合并多次提交有部分写入风险；价格输入/汇率/队列/撤销边界需加固。

## v18 工作区缓存与去重隔离
发现商品列表缓存和 count 缓存未含工作区，去重两表遗漏租户 SQL 改写。已补租户表、按工作区分缓存及索引目录/签名，避免跨工作区数据返回。无 schema 变更。
验证：test_v18_isolation：2 passed。首跑因共享机器负载 256 和 BLAS 线程争抢超时；限制测试 BLAS 线程后 3.3 秒通过。
下一轮：原子索引快照与损坏校验

## v19 单文件原子索引快照
v15 多文件分别 replace 允许读到不同代的 ids/index；现改为单个 NPZ 快照、一次 fsync+replace 发布，内含 JSON/ids/向量/序列化 FAISS 与 SHA256；严格校验 shape/dtype/条数/维度/有限值，损坏回退重建。v1 缓存失效，不迁移商品数据。
验证：test_v19_snapshots + test_v15_faiss_persist：13 passed；包含并发 20 次发布/读取、注入 replace 失败保留旧代、篡改 payload 校验。
下一轮：语义检索并发与模型缓存一致性

## v20 搜索并发与模型缓存身份
语义搜索共用可变 _INDEX，另一请求可能在检索前替换商品顺序。使用可重入锁覆盖索引构建/检索/失效，确保结果位置对应同一商品快照；embedding 文件绑定模型+归一化版本并原子替换。旧无模型元数据缓存自动忽略。
验证：test_v20_search_consistency + test_v15_faiss_persist：12 passed；并发 40 次交替顺序查询均返回正确 id，模型切换拒绝旧 embedding。
下一轮：跨平台安全并行和稀疏评分

## v21 Windows 回退与服务进程安全评分
审计发现稀疏/回调相似度在 fork worker 中被当作 0，且 ASGI 工作线程 fork 存在继承锁死锁风险。仅单线程 CLI 的 dense 评分允许 fork；Windows/spawn、稀疏、服务多线程统一正确串行回退；清理池共享状态、顺序收集结果，失败有日志。
验证：test_v21_portable_scores + test_v16_dedup_scale：8 passed；模拟仅 spawn、线程内禁 fork、20301 对稀疏回调与串行一致。未在 Windows 真机执行，未重新声称 1M 性能。
下一轮：合并与撤销的事务原子性

## v22 合并及撤销原子事务
合并逐条 commit，故障会留下已搬价格但没有审计记录。新增线程本地事务/嵌套 savepoint，SQLite BEGIN IMMEDIATE 串行读改写，merge/unmerge 全部同一提交；缺失商品明确 404。没有 schema 迁移，不需要修改生产库或备份迁移。
验证：test_v22_transactions + test_v12_dedup：7 passed；注入审计写入故障验证价格/商品/审计全部回滚，嵌套回滚和合并撤销往返通过。PostgreSQL 分支未在真实服务器验证。
下一轮：价格输入与历史时序

## v23 价格合法性与事件时间历史
人工和采集写入统一拒绝 NaN/Inf/负数/布尔价格，缺失商品不写孤儿价格；ISO 时区归一 UTC。最新价按事件时间而不是插入 id，回填历史不会盖掉现价。预测/统计忽略遗留非法值并按有效日期排序。
验证：test_v23_price_history + test_v17_stability + test_v12_dedup：16 passed；覆盖 6 类非法值、偏移时区、回填价格及预测清洗。未迁移已有旧格式时间。
下一轮：跨币种比价与汇率输入

## v24 跨币种比价正确性
原比较接口直接按不同币种的数值排序。现按配置参考汇率统一 CNY 比价/价差；未知币种显式列为 unconverted，不参与最便宜；定价竞品换到目标币种。深度对比混合币种取消原价最优标记并提示；汇率拒绝零/负/非有限值及变更 CNY 基准。
验证：test_v24_currency + test_v17_stability：5 passed；100 CNY 与 20 USD（配置 7.0）正确排序、未知币种排除、坏汇率不落库；UI 仅静态断言，未声称看过页面。
下一轮：并发采集 upsert 与提交后事件
