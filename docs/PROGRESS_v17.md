# PriceHub v17 进度：搜索/比价稳定性与启动打磨

时间：2026-11-24（UTC+8）

## 为什么做这些

跑过 v12–v16 相关测试并核对搜索、比价、就绪探针和 README 之后，实际会影响使用的问题是：接口在坏输入或坏数据时直接 500、商品列表被损坏的 `attrs` 拖垮、`/readyz` 某个检查抛错会让整个探针失败、启动脚本在没有虚拟环境时静默失败、文档里的启动方式与现在的脚本不完全一致。这一轮只修这些，每条都有测试或可复现验证。

未把测试里因本环境缺少 Playwright / Prophet / sentence-transformers / 磁盘紧张导致的失败当成产品缺陷来“修”。

## 做了什么

### 搜索 / 比价路径
- `/api/search/semantic`：空查询返回 400；搜索异常返回 503（中文说明），不再未处理 500。`semantic()` 自身也处理空查询、非法 k、TF-IDF 空词汇表。
- `/api/compare/deep`：非法 id 返回 400，商品不存在返回 404；不再因 `int("abc")` 变成 500。
- `/api/search/image`：空文件或无法识别返回 400，临时文件在 `finally` 里删除。
- `/api/products` 缺少标题返回 400；`/api/products/{id}/compare` 的 404 带上「商品不存在」。
- 单条商品没有价格历史时，预测接口仍返回 JSON（置信度计算失败不 500）。
- 商品 `attrs` 不是合法 JSON 时，列表和详情回退为 `{}`，工作台不会整页失败。
- 前端 `deepRows()` 在没有 `items` 时返回空数组，避免错误态把 Alpine 渲染打挂。

### 启动可靠性
- `/readyz` 的数据库、调度器、渲染器、插件、向量索引各自捕获异常；只有数据库不可用才 503。向量检查增加 `source` / `n`，便于看索引是磁盘加载还是重建。
- `start_server.sh`：没有 `.venv/bin/uvicorn` 时打印安装提示并以状态码 1 退出（不再 nohup 一个不存在的命令）；自动 `mkdir -p logs`。

### 文档
- README 改为以 `./start_server.sh` 为准（默认 SQLite `data/pricehub.db`），并写明 `/readyz`、`PRICEHUB_NO_ST` / `PRICEHUB_FAISS_DIR` / `PRICEHUB_SCORE_WORKERS` 等环境变量；再次说明不要把密钥和数据库提交进仓库。

## 验证

- 新增 `tests/test_v17_stability.py`：**3 passed**（独立端口 + 临时库）。覆盖就绪探针 200、空搜索 400、缺标题 400、非法/缺失 id 的深度对比 400/404、正常对比 200、损坏 attrs 下列表与详情仍 200、启动脚本缺 venv 时退出 1、TF-IDF 语义搜索单元。
- 回归：`test_v15_faiss_persist` 10、`test_v16_dedup_scale` 5、`test_v14_perf` 3、`test_v13_blocking` 5，全部通过（合计 **26 passed**）。

## 未做

- 官方 API 真机联调、TimescaleDB、移动端、Windows 安装包
- 用真实多语言模型在 10 万以上评估；真实数据召回
- pgvector 版近邻检索
