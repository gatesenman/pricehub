import numpy as np
from app.domains.dedup import parallel as P
from app.domains.dedup.features import extract

def test_no_fork_windows_fallback(monkeypatch):
    import multiprocessing as mp
    monkeypatch.setattr(mp,'get_all_start_methods',lambda:['spawn'])
    assert P.parallel_backend(4,np.ones(10))=='sequential:no-fork'

def test_sparse_callback_not_silently_zeroed():
    n=202
    ps=[{'id':i,'title':'Apple A100 256GB','platform':'x'} for i in range(n)]
    feats=[extract(p) for p in ps]
    codes=np.array([i*n+j for i in range(n) for j in range(i+1,n)],dtype=np.int64)
    assert len(codes)>20000
    one=P.score_candidate_codes(codes,n,ps,feats,simf=lambda i,j:1.0,workers=1)
    four=P.score_candidate_codes(codes,n,ps,feats,simf=lambda i,j:1.0,workers=4)
    assert len(one)==len(four)==len(codes)
    assert [(i,j,s) for i,j,s,*_ in one]==[(i,j,s) for i,j,s,*_ in four]

def test_threaded_service_never_forks():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as ex:
        assert ex.submit(P.parallel_backend,4,np.ones(10)).result()=='sequential:threaded-process'
