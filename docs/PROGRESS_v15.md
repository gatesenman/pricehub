# PriceHub v15 进度：FAISS 索引持久化

时间：2026-11-24（UTC+8）

## 做了什么

重启后第一次语义搜索 / 去重扫描不再从零重建向量索引。

- **落盘格式**（`app/core/vector.py`）：`pricehub-faiss-v1`。每个索引一组文件：`.meta.json`（签名）+ `.ids.npy` + FAISS `.index`（HNSW/Flat）和/或 `.vecs.npy`（numpy 精确，或去重 ANN 需要的向量矩阵，供 `sim()` 使用）。写入先写临时文件再 `os.replace`，避免半截文件。
- **签名失效**：签名包含格式版本、名称、条数、维度、内容摘要（商品 id + 标题文本，或向量字节）、可选引擎参数与模型名。摘要变了（增删改标题）、条数或维度不符、文件损坏或不是 JSON，一律不加载并走重建。`kind=auto` 不参与比较，以免尚未建索引时无法预知精确还是 HNSW。
- **启动加载**：`main._warm_search_models` 在预热 ST/CLIP 之后调用 `warm_persisted_index()`，当前商品签名与磁盘一致则直接载入。搜索缓存键改为内容签名，不再跟 `db.VER` 走——记一条价格不再误触发整表重建。
- **写入 / 删除时更新**：
  - 新商品：搜索索引增量 `add` 并落盘（内存里已有索引且能编码时）；否则失效。
  - 改标题、删除、撤销删除、合并 / 撤销合并：失效内存和磁盘索引（HNSW 不便按 id 删除），下次搜索或扫描再重建。
- **去重 ANN**：`BlockIndex.save_ann` / `load_ann`。`_block_index` 先重建键/LSH 桶（快），再加载磁盘 dense 向量 + HNSW；命中则跳过建索引。增量 `add` 成功后也会回写。稀疏 TF-IDF 不落盘（重建便宜且词汇表依赖全集）。
- **目录**：`PRICEHUB_FAISS_DIR` 优先，否则 `{PRICEHUB_DB}.faiss/`，避免测试库写到生产索引。已加入 `.gitignore`（`data/faiss/`、`*.db.faiss/`），不入库。

未做：官方 API 联调、TimescaleDB、移动端、Windows 安装包、pgvector 近邻检索。

## 验证

- 新增 `tests/test_v15_faiss_persist.py`：**10 passed**。覆盖 numpy/Flat/HNSW 往返与检索一致、签名不符与损坏文件不加载、增量 add 落盘、删除失效、semantic 启动加载与商品钩子、去重 ANN 近邻与 `sim()` 一致。
- 回归：`test_v13_blocking` 5 个、`test_v12_dedup` 5 个，全部通过（合计 20 passed）。

### 冷启动对比（本机，哈希随机向量，64 维；加载后 top-10 与重建完全一致）

| 规模 | 引擎 | 从向量重建 | 落盘 | 从磁盘加载 | 加速 |
|---|---|---|---|---|---|
| 2 万 | FAISS HNSW | 0.940 s | 0.021 s | 0.008 s | 116× |
| 5 万 | FAISS HNSW | 2.796 s | 0.034 s | 0.019 s | 147× |
| 5 万 | numpy 精确 | ≈0 s（只包一层矩阵） | 0.015 s | 0.004 s | — |

pytest 内同条件 2 万 HNSW：重建 0.860 s，加载 0.0095 s，约 91×。

## 磁盘

本轮开始时根分区只剩约 608 MB，安装测试依赖失败。只清理了 PriceHub 自己的文件：`build_win/cache`、半残 `.venv`、以及本轮不打包用的 `build_win/wineprefix`（约 1.2 GB）。未动其他项目，也未删除 `data/pricehub.db`。

## 剩余

- 去重 100 万打分并行化、LSH 超大块收紧（v16）
- pgvector 版近邻检索
- 用真实多语言模型在 10 万以上评估；真实数据召回
