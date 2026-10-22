# v11（定向派发 + pgvector）进度
时间均为北京时间（UTC+8）。

## 1. 按地域 / 标签定向派发
- 节点声明：`python -m app.worker --region cn --tags playwright,residential-proxy`（或环境变量 PRICEHUB_NODE_REGION / PRICEHUB_NODE_TAGS），随心跳上报。
- 任务要求：payload `_require = {region: cn | [cn,hk], tags: [...], fallback: any|wait|fail}`；地域满足其一、标签需全部具备。
- 来源：数据源配置里的 `require` 优先；否则按「派发规则」（按平台域名 / 数据源类型 / `*` 匹配，第一条命中生效，存于数据库文件旁的 `.routing.json`）。
- Redis 后端：不匹配的节点把任务放回队列（deferred 计数），由匹配节点领取；没有在线匹配节点时按回退规则：
  any = 任意节点执行（标记 fallback）、wait = 排队等匹配节点上线、fail = 直接进死信。排空中的节点不算在线。
- 内存后端：单节点按同一规则判断。
- API：`GET/PUT /api/routing/rules`、`POST /api/routing/preview`、`POST /api/nodes/probe`（可带 require）。任务视图带 route / route_desc。
- 界面：采集节点页新增「地域 / 标签」列、按要求派发探测、派发规则编辑表。
- 端到端（tests/test_v11_routing.py）：cn+playwright 与 us+residential-proxy 两个 worker，按地域、按标签、组合条件只落到匹配节点；
  jp 要求在 any/fail/wait 三种回退下的行为；wait 的任务在第 3 个 jp worker 上线后由它执行；平台规则与数据源 require 优先级。

## 2. pgvector
- 本机 apt 源有 `postgresql-17-pgvector 0.8.0`，已安装并在 pricehub_test、template1 中 `CREATE EXTENSION vector`（不需要源码编译）。
- 接入：`PgVectorIndex`（每组向量一张表 + HNSW 索引，按内容哈希复用，进程重启或多进程无需重建），`FallbackIndex` 出错自动回退 FAISS / numpy。
  选择规则 `PRICEHUB_VECTOR_ENGINE=auto|pgvector|faiss|numpy`：auto 时 PostgreSQL + 扩展可用且商品数 ≥ 5 万才用 pgvector（小规模进程内 numpy 更快）。
  语义搜索接口返回 `vector_engine`；/readyz 显示当前引擎与 pgvector 状态。
- 压测（scripts/bench_vector.py，384 维、1000 簇、200 条查询，真值为精确检索；原始数据 docs/bench_vector_v11.jsonl）：

| 规模 | 引擎 | 召回@10 | p50 ms | p95 ms | 建索引 s |
|---|---|---|---|---|---|
| 2 万 | numpy 精确 | 1.000 | 0.24 | 1.31 | — |
| 2 万 | FAISS HNSW(M32,ef64) | 0.962 | 0.24 | 0.27 | 3.2 |
| 2 万 | pgvector HNSW(m16,ef40/100/200) | 0.933 / 0.955 / 0.969 | 1.19 / 1.79 / 2.81 | 1.75 / 2.17 / 3.33 | 5.6 |
| 10 万 | numpy 精确 | 1.000 | 3.88 | 13.31 | — |
| 10 万 | FAISS HNSW(M32,ef64) | 0.996 | 0.23 | 0.32 | 19.7 |
| 10 万 | pgvector HNSW(m16,ef40/100/200) | 0.934 / 0.994 / 0.995 | 1.92 / 2.90 / 5.38 | 3.59 / 4.52 / 6.38 | 26.8（复用 0.34） |

  结论：单机延迟 FAISS 最快（进程内、无网络往返）；pgvector 在 ef=100 时召回与 FAISS 接近、延迟约 3 ms，
  优势是持久化与多进程/多机共享（重启复用 0.3 秒，FAISS 需重建约 20 秒）。默认 ef_search=100。
- 测试：tests/test_v11_pgvector.py（与精确检索一致、复用、回退、自动选择阈值、SQLite 下说明不可用、PG 语义搜索走 pgvector）。

## 3. 其他
- 第 41、42 个商品来源：见最终报告（数据源 6「平台:PChome 24h購物(台湾)」定时采集新上架的 iPhone 17e）。
- 仍未做：真实官方 API 签名与联调（需密钥）、TimescaleDB/ClickHouse（apt 源无包）、移动端。
