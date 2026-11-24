from hardening_support import probe

def test_search_order_and_concurrent_collections():
    probe('''
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from app.domains.search import semantic as S
class Model:
    def encode(self, texts, **kw):
        return np.array([[1.,0.] if 'apple' in t else [0.,1.] for t in texts],dtype=np.float32)
S._ST=Model()
a=[{'id':1,'title':'apple'},{'id':2,'title':'pear'}]
b=list(reversed(a))
def work(i):
    ps=a if i%2 else b
    assert S.semantic('apple',ps,1)[0]['id']==1
with ThreadPoolExecutor(4) as pool: list(pool.map(work,range(40)))
''')

def test_model_change_rejects_embedding_cache():
    probe('''
import numpy as np,os
from app.domains.search import semantic as S
S._CACHE['x']=np.array([1.,0.],dtype=np.float32)
S._save_disk(); S._CACHE.clear()
os.environ['PRICEHUB_TEXT_MODEL']='different-model'
S._load_disk(); assert not S._CACHE
os.environ.pop('PRICEHUB_TEXT_MODEL')
S._load_disk(); assert 'x' in S._CACHE
''')
