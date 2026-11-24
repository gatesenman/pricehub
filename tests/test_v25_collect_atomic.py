from hardening_support import probe

def test_concurrent_upsert_preserves_metadata_and_history():
    probe('''
from concurrent.futures import ThreadPoolExecutor
from app import main as M, db
base={'title':'initial','platform':'local','external_id':'stable','attrs':{'capacity':'256GB'}}
pid=M.upsert(None,base)
def collect(i): return M.upsert(None,{'title':'new','platform':'local','external_id':'stable','seller':'seller','price':100+i})
with ThreadPoolExecutor(6) as pool: ids=list(pool.map(collect,range(12)))
assert set(ids)=={pid}
assert len(db.q('SELECT * FROM products'))==1
assert len(db.q('SELECT * FROM prices'))==12
p=M.product(pid)
assert p['attrs']=={'capacity':'256GB'} and p['title']=='new'
''')

def test_failed_variant_rolls_back_and_no_events():
    probe('''
from app import main as M, db
from fastapi import HTTPException
calls=[]
M.bus.subscribe('price.recorded',lambda topic,**kw:calls.append(kw),'test.v25')
try:
    M.upsert(None,{'title':'parent','platform':'local','external_id':'p','price':10,
        'variants':[{'name':'ok','price':5},{'name':'bad','price':-1}]})
except HTTPException: pass
else: raise AssertionError('negative variant')
assert not db.q('SELECT * FROM products') and not db.q('SELECT * FROM prices') and not calls
with db.transaction():
    db.after_commit(lambda:calls.append('outer'))
    try:
        with db.transaction():
            db.after_commit(lambda:calls.append('discard'))
            raise ValueError()
    except ValueError: pass
    assert not calls
assert calls==['outer']
''')


def test_image_io_runs_outside_write_transaction():
    probe('''
from app import main as M,db
called=[]
def image(url,pid):
    assert getattr(db._TX,'connection',None) is None
    assert db.q('SELECT id FROM products WHERE id=?',(pid,),one=True)
    called.append(pid)
M._save_image=image
pid=M.upsert(None,{'title':'image','platform':'local','external_id':'img','image_url':'mock://image'})
assert called==[pid]
assert M._safe_attrs('[]')=={} and M._safe_attrs('123')=={}
''')
