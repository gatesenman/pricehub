from hardening_support import probe

def test_undo_expiry_workspace_and_reused_id():
    probe('''
from app import main as M,db
from app.api import experience as E
from app.core import tenancy as T
from fastapi import HTTPException
pid=M.add_product({'title':'old','platform':'local'})['id']
r=E.products_bulk({'ids':[pid],'action':'delete'})
token=r['undo']
with T.use(2):
    try: E.undo(token)
    except HTTPException as e: assert e.status_code==404
    else: raise AssertionError('cross workspace undo')
assert token in E.UNDO
new=M.add_product({'title':'new','platform':'local'})['id']
assert new==pid  # SQLite may reuse deleted integer primary keys
try: E.undo(token)
except HTTPException as e: assert e.status_code==409
else: raise AssertionError('overwrite')
assert M.product(new)['title']=='new' and token in E.UNDO
E.UNDO[token]['at']-=601
try: E.undo(token)
except HTTPException as e: assert e.status_code==404
else: raise AssertionError('expired token')
''')

def test_related_rows_roundtrip_and_merge_guard():
    probe('''
from app import main as M,db
from app.api import experience as E
from fastapi import HTTPException
pid=M.add_product({'title':'root','platform':'local','price':10})['id']
other=M.add_product({'title':'other','platform':'local'})['id']
db.ex("INSERT INTO rules(product_id,name) VALUES(?,?)",(pid,'r'))
db.ex("INSERT INTO dedup_candidates(a_id,b_id,score) VALUES(?,?,?)",(pid,other,.8))
r=E.products_bulk({'ids':[pid],'action':'delete'})
assert not db.q('SELECT * FROM rules') and not db.q('SELECT * FROM dedup_candidates')
E.undo(r['undo'])
assert M.product(pid)['title']=='root' and len(db.q('SELECT * FROM rules'))==1
assert len(db.q('SELECT * FROM dedup_candidates'))==1
db.ex('UPDATE products SET merged_into=? WHERE id=?',(pid,other))
try: E.products_bulk({'ids':[pid],'action':'delete'})
except HTTPException as e: assert e.status_code==409
else: raise AssertionError('deleted canonical')
assert len(db.q('SELECT * FROM products'))==2
''')
