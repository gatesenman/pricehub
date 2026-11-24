"""v16：大规模去重打分。先分块向量化预筛，再并行 score_pair；避免把数千万对一次性变成 Python 集合。

多进程只用 fork（子进程 COW 只读父进程的商品/向量，不把 100 万条记录 pickle 出去）。
fork 不可用或失败时自动回退单进程。服务进程里若已有多线程，fork 可能不安全，可用 PRICEHUB_SCORE_WORKERS=1 关闭。
"""
import os, threading, logging
import numpy as np

_STATE = {}
_POOL_LOCK = threading.Lock()
log = logging.getLogger("pricehub.dedup.score")

def parallel_backend(workers, sims):
    """fork is allowed only in a single-threaded CLI, never an ASGI worker thread.
    Windows/spawn and sparse/callback scoring use the same sequential scorer.
    """
    if workers <= 1: return "sequential"
    if sims is None: return "sequential:callback"
    import multiprocessing as mp
    if "fork" not in mp.get_all_start_methods(): return "sequential:no-fork"
    if threading.current_thread() is not threading.main_thread() or threading.active_count() != 1:
        return "sequential:threaded-process"
    return "fork"



def default_workers(n_codes=0):
    env = os.environ.get("PRICEHUB_SCORE_WORKERS")
    if env is not None and env != "auto":
        try:
            return max(1, int(env))
        except ValueError:
            return 1
    if n_codes < 20000:
        return 1
    cpu = os.cpu_count() or 2
    return max(1, min(4, max(1, cpu // 2)))


def prefilter_codes(codes, n, V, has_ids, sim_floor=0.45, chunk=250000):
    """分块计算 V[i]·V[j]，去掉既无条码、相似度又低于门槛的对。返回 (codes, sims)。"""
    if codes is None or len(codes) == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    codes = np.asarray(codes, dtype=np.int64)
    if V is None:
        return codes, None
    has_ids = np.asarray(has_ids, dtype=bool)
    keep_c, keep_s = [], []
    for st in range(0, len(codes), chunk):
        c = codes[st:st + chunk]
        i, j = c // n, c % n
        s = (V[i] * V[j]).sum(axis=1).astype(np.float32, copy=False)
        ok = (s >= sim_floor) | (has_ids[i] & has_ids[j])
        if ok.any():
            keep_c.append(c[ok]); keep_s.append(s[ok])
    if not keep_c:
        return np.zeros(0, np.int64), np.zeros(0, np.float32)
    return np.concatenate(keep_c), np.concatenate(keep_s)


def _worker_score(payload):
    from app.domains.dedup.service import score_pair
    codes, sims = payload
    ps, F, eng, thr, n = _STATE["ps"], _STATE["F"], _STATE["engine"], _STATE["thr"], _STATE["n"]
    out = []
    for k, code in enumerate(codes):
        i = int(code // n); j = int(code % n)
        sij = float(sims[k]) if sims is not None else 0.0
        if sij < 0.45 and not (F[i]["ids"] and F[j]["ids"]):
            continue
        sc, why, sig = score_pair(ps[i], ps[j], F[i], F[j], sij, eng)
        if sc >= thr:
            out.append((i, j, sc, why, sig))
    return out


def score_candidate_codes(codes, n, ps, F, *, vec=None, simf=None, engine="向量",
                          threshold=0.70, workers=None, chunk=50000):
    """对候选编码打分，只返回达到阈值的 (i, j, score, why, sig)。"""
    from app.domains.dedup.service import score_pair
    import scipy.sparse as sp
    codes = np.asarray(codes, dtype=np.int64)
    if len(codes) == 0:
        return []
    V = None
    if vec is not None and not sp.issparse(vec):
        V = np.ascontiguousarray(vec, dtype=np.float32)
    has = np.array([bool(f.get("ids")) for f in F], dtype=bool)
    sims = None
    if V is not None:
        codes, sims = prefilter_codes(codes, n, V, has)
    workers = default_workers(len(codes)) if workers is None else max(1, int(workers))

    def run_one(c, s):
        out = []
        for k, code in enumerate(c):
            i = int(code // n); j = int(code % n)
            if s is not None:
                sij = float(s[k])
            elif simf is not None:
                sij = float(simf(i, j))
            elif V is not None:
                sij = float(V[i] @ V[j])
            else:
                sij = 0.0
            if sij < 0.45 and not (F[i]["ids"] and F[j]["ids"]):
                continue
            sc, why, sig = score_pair(ps[i], ps[j], F[i], F[j], sij, engine)
            if sc >= threshold:
                out.append((i, j, sc, why, sig))
        return out

    def run_all_seq():
        found = []
        for st in range(0, len(codes), chunk):
            sl = slice(st, st + chunk)
            found.extend(run_one(codes[sl], None if sims is None else sims[sl]))
        return found

    backend=parallel_backend(workers,sims)
    if len(codes) < 20000 or backend != "fork":
        return run_all_seq()
    # One pool owns shared state at a time; lock is acquired non-blocking before fork.
    if not _POOL_LOCK.acquire(blocking=False): return run_all_seq()
    try:
        import multiprocessing as mp
        _STATE.update(ps=ps, F=F, engine=engine, thr=float(threshold), n=int(n))
        chunks = ((codes[st:st+chunk], sims[st:st+chunk]) for st in range(0,len(codes),chunk))
        found=[]
        with mp.get_context("fork").Pool(processes=workers) as pool:
            for part in pool.imap(_worker_score,chunks,chunksize=1): found.extend(part)
        return found
    except Exception as e:
        log.warning("parallel scoring failed; sequential fallback: %s",type(e).__name__)
        return run_all_seq()
    finally:
        _STATE.clear()
        _POOL_LOCK.release()
