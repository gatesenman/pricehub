import numpy as np
from app.core import vector as V

def test_atomic_failure_preserves_previous_generation(tmp_path, monkeypatch):
    x=np.eye(8,dtype=np.float32)
    vi=V.VectorIndex(kind='numpy').build(x)
    sig=V.make_signature(name='test',kind='auto',dim=8,n=8,digest='one',params={'k':7})
    V.save_vector_index(vi,'test',sig,root=str(tmp_path))
    def fail(*a): raise OSError('injected disk failure')
    with monkeypatch.context() as m:
        m.setattr(V.os,'replace',fail)
        import pytest
        with pytest.raises(OSError): V.save_vector_index(vi,'test',{**sig,'digest':'two'},root=str(tmp_path))
    loaded,why=V.load_vector_index('test',sig,root=str(tmp_path))
    assert why=='ok' and loaded.size()==8
    assert not list(tmp_path.rglob('.snapshot-*'))

def test_payload_corruption_detected(tmp_path):
    vi=V.VectorIndex(kind='numpy').build(np.eye(4,dtype=np.float32))
    V.save_vector_index(vi,'test',root=str(tmp_path))
    path=V.index_paths('test',str(tmp_path))['snapshot']
    with np.load(path,allow_pickle=False) as z: data={k:z[k] for k in z.files}
    data['vectors'][0,0]=123
    np.savez(path,**data)
    assert V.load_vector_index('test',root=str(tmp_path))==(None,'corrupt')

def test_concurrent_generation_readers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    def write(i):
        vi=V.VectorIndex(kind='numpy').build(np.eye(8,dtype=np.float32)*(i+1))
        V.save_vector_index(vi,'test',root=str(tmp_path))
        got,why=V.load_vector_index('test',root=str(tmp_path))
        assert why=='ok' and got.M.shape==(8,8)
        assert len(np.unique(got.M.diagonal()))==1
    with ThreadPoolExecutor(4) as pool: list(pool.map(write,range(20)))
