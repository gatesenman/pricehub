"""Reproducible synthetic index build/save/load probe. No database/model/network access."""
import argparse,json,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from app.core.vector import VectorIndex,content_digest,make_signature,save_vector_index,load_vector_index

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--n',type=int,default=20000)
    parser.add_argument('--dim',type=int,default=64)
    args=parser.parse_args()
    if not 1<=args.n<=100000 or not 1<=args.dim<=1024: parser.error('n 1..100000, dim 1..1024')
    x=np.random.RandomState(17).normal(size=(args.n,args.dim)).astype('float32')
    x/=np.linalg.norm(x,axis=1,keepdims=True)+1e-9
    t=time.perf_counter(); vi=VectorIndex(kind='hnsw').build(x); build=time.perf_counter()-t
    sig=make_signature(name='bench',kind='auto',dim=args.dim,n=args.n,digest=content_digest(vi.ids,vectors=x))
    with tempfile.TemporaryDirectory(prefix='pricehub-snapshot-bench-') as root:
        t=time.perf_counter(); save_vector_index(vi,'bench',sig,root=root); save=time.perf_counter()-t
        t=time.perf_counter(); loaded,reason=load_vector_index('bench',sig,root=root); load=time.perf_counter()-t
        assert reason=='ok'
        a,b=vi.search(x[args.n//2],10); c,d=loaded.search(x[args.n//2],10)
        same=bool(np.array_equal(a,c) and np.allclose(b,d))
        assert same
        print(json.dumps(dict(dataset='synthetic-normalized-random-vectors',n=args.n,dim=args.dim,
              build_s=round(build,6),save_s=round(save,6),load_s=round(load,6),top10_identical=same)))
if __name__=='__main__':main()
