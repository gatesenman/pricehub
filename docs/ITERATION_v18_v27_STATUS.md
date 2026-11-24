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

## v25 采集写入并发与提交后副作用
同 external_id 并发读后插存在竞态，且 seller-only 更新会清空 attrs。upsert/record_price 使用事务，SQLite BEGIN IMMEDIATE 覆盖主商品/规格/价格；仅更新提供的元数据。新增 after_commit：事务失败不发价格事件、不发布半成品索引，savepoint 回滚丢弃对应回调。
验证：test_v25_collect_atomic + test_v22_transactions + test_v23_price_history：12 passed；6 线程 12 次同 SKU 写入仅 1 商品和 12 历史；坏规格导致全部回滚且事件计数 0。
下一轮：调度线程存活与上下文隔离

## v26 采集调度存活与上下文隔离
未知异常/路由异常会直接杀死工作线程；并发 start 也可能多启线程。现启动过程持条件锁，所有任务捕获未归类异常并终结/写死信，后续任务继续；每个 Job 保存独立 contextvars 工作区；令牌桶速率拒绝零/负/非有限值，stop 可等待线程退出。
验证：test_v26_scheduler + test_v6_platform + test_v11_routing：结果见 iteration-results/v26.txt；新 7 项通过（异常后线程存活、32 并发任务工作区一致、限速非法值）。
下一轮：批量删除/撤销的数据完整性和工作区授权

## v27 安全批量删除与撤销
撤销令牌原来没有检查工作区、消费时没有检查过期，INSERT OR REPLACE 会覆盖删除后重用 id 的新商品。现令牌绑定工作区+600 秒有效期、互斥消费、事务恢复使用 INSERT，冲突 409 且不覆盖任何行；批删同步保存/删除关联提醒/证据/去重候选，禁止直接删仍有合并页或规格的商品。测试图片/证据目录支持显式隔离。
验证：test_v27_safe_undo + test_v18_isolation + test_v22_transactions：6 passed；十轮及 v12–v17 相关汇总：64 passed（45.79s）。令牌跨工作区、过期、id 复用保护、关联行删除/恢复已验证。
下一轮：完整测试尝试、审计待推送范围、统一推送

## 最终汇总（2026-11-24，Asia/Shanghai，UTC+8）

十轮实现均已独立提交。补充整体验证时修复：LSH 大桶窗口按哈希排序后可能返回反向编码，现统一 min/max；图片下载/特征提取挪到提交后，不占数据库写事务；合法 JSON 但不是对象的 attrs 也回退 {}。

- 十轮及相关回归（最终）：**77 passed，4 skipped，37.60s**。覆盖 v12–v27 改动、调度器和路由；跳过项为本机缺 Redis 的场景。
- 完整测试尝试（补充两项回归前）：**111 passed，13 skipped，10 failed，12 collection errors，302.70s**，不能称“全量通过”。
  - 12 个 collection errors：未安装 Playwright 的 UI 文件，未打开/查看网页。
  - 3 个跨语言检索失败 + 1 个旧同款建议失败：本环境关闭/未安装 ST，TF-IDF 不具备等价多语言召回；不能当真实数据召回保证。
  - 3 个渲染/取证/浏览器设置失败：缺 Playwright/浏览器运行环境。
  - 2 个 STL/ARIMA 引擎断言失败：缺 statsmodels（Prophet 相关按测试既有规则跳过）。
  - 1 个 monitor/run 超时：全套时未在 60 秒内完成；独立重测、将本地采集重试退避 PRICEHUB_BACKOFF_S 设为 0.05 后，基础 e2e **20 passed，6 deselected，12.83s**。尚不能称默认退避下全套已排除超时。
- `node --check app/static/app.js` 通过；跨币种 UI 变更有静态断言，没有浏览器截图验证。
- 没有重新跑 1M 或真实商品数据；v16 的速度/召回只是此前合成基准，服务多线程和 Windows 现在有意选择安全串行回退，不再套用 CLI 并行速度。

### 当前快照格式微基准

命令：`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/bench_index_snapshot.py`

```json
{"dataset": "synthetic-normalized-random-vectors", "n": 20000, "dim": 64, "build_s": 0.977898, "save_s": 0.193458, "load_s": 0.031229, "top10_identical": true}
```

数据仅为 20,000×64 归一化随机合成向量。含校验和的原子快照：重建 0.978s、写盘 0.193s、加载 0.031s；加载后 top-10 相同。不包括模型加载/文本编码，不代表生产服务启动或真实召回。

### 提交索引

- `0f36a50` fix(tenancy): isolate product caches dedup rows and index namespaces (v18)
- `e57fc63` fix(vector): publish checksummed index snapshots as one atomic generation (v19)
- `3aa66b4` fix(search): synchronize index snapshots and version embedding caches (v20)
- `07098ca` fix(dedup): preserve callback scores and restrict fork to safe CLI use (v21)
- `f61ebf2` fix(storage): add nested transactions for atomic dedup merge and undo (v22)
- `d098c6d` fix(prices): validate amounts and order history by normalized event time (v23)
- `92ef07f` fix(compare): normalize currencies and reject invalid FX configurations (v24)
- `5f1aa47` fix(collect): serialize upserts and defer side effects until transaction commit (v25)
- `35b2b3a` fix(scheduler): survive unexpected failures and isolate job contexts (v26)
- `95f0bb6` fix(catalog): scope undo tokens and restore deleted products without overwriting (v27)

### 刻意保留/风险
- 未部署、未改 :8800 进程、未修改 data/pricehub.db；日志/截图原有脏文件未纳入提交。
- 不请求用户 API 密钥，不跑真实通知，不构建 Windows 安装包、不传用户电脑。
- Windows 仅模拟无 fork/多线程回退；PostgreSQL 事务分支未在真实 PG 服务验证；未做新 schema/data 迁移。
- 参考汇率非实时；未知币种不参与最低价比较。旧历史时间未批量迁移。
- 索引 v1 缓存升级 v2 会自动重建；进程内语义检索为一致性串行化，尚未做高并发吞吐评估。
- 撤销仅同进程 10 分钟有效，id 已复用时安全拒绝恢复（不覆盖新数据），非持久化撤销历史。
- 下一步：完整依赖/Windows/PG 独立验证，真实脱敏商品召回评估，以及默认重试配置的 monitor/run 超时排查。
