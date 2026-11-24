from hardening_support import probe

def test_workspace_caches_and_dedup_rows():
    probe('''
from app import main as M, db
from app.core import tenancy as T
from app.core.vector import index_paths
for ws in (1,2):
    with T.use(ws):
        db.ex("INSERT INTO products(platform,external_id,title) VALUES(?,?,?)",('local',str(ws),'workspace-'+str(ws)))
        db.ex("INSERT INTO dedup_candidates(a_id,b_id,score) VALUES(?,?,?)",(ws,ws+10,0.8))
with T.use(1):
    assert M.products()[0]['title']=='workspace-1'
    assert len(db.q('SELECT * FROM dedup_candidates'))==1
    a=index_paths('search')['meta']
with T.use(2):
    assert M.products()[0]['title']=='workspace-2'
    assert len(db.q('SELECT * FROM dedup_candidates'))==1
    b=index_paths('search')['meta']
assert a!=b
with T.use(1):
    db.ex('DELETE FROM dedup_candidates')
with T.use(2):
    assert len(db.q('SELECT * FROM dedup_candidates'))==1
''')

def test_signature_workspace_namespace():
    probe('''
from app.core import tenancy as T
from app.domains.search.semantic import search_signature
p=[{'id':1,'title':'same'}]
with T.use(1): a=search_signature(p)
with T.use(2): b=search_signature(p)
assert a['digest']!=b['digest']
''')
