from hardening_support import probe

def test_nested_transactions_and_rollback():
    probe('''
from app import db
db.init()
try:
    with db.transaction():
        db.ex("INSERT INTO products(title) VALUES('outer')")
        with db.transaction(): db.ex("INSERT INTO products(title) VALUES('inner')")
        raise RuntimeError('injected')
except RuntimeError: pass
assert not db.q('SELECT * FROM products')
with db.transaction():
    db.ex("INSERT INTO products(title) VALUES('keep')")
    try:
        with db.transaction():
            db.ex("INSERT INTO products(title) VALUES('rollback')")
            raise ValueError()
    except ValueError: pass
assert [p['title'] for p in db.q('SELECT * FROM products')]==['keep']
''')

def test_merge_injected_failure_rolls_back():
    probe('''
from app import db
from app.domains.dedup import service as S
db.init()
a=db.ex("INSERT INTO products(title) VALUES('a')")
b=db.ex("INSERT INTO products(title) VALUES('b')")
db.ex('INSERT INTO prices(product_id,price) VALUES(?,?)',(b,10))
c=db.ex("INSERT INTO dedup_candidates(a_id,b_id,score,reasons) VALUES(?,?,?,?)",(a,b,.99,'[]'))
orig=db.ex
def fail(sql,args=()):
    if sql.startswith('INSERT INTO product_merges'): raise RuntimeError('injected')
    return orig(sql,args)
db.ex=fail
try: S.merge(c,canonical_id=a)
except RuntimeError: pass
else: raise AssertionError('expected failure')
db.ex=orig
assert db.q('SELECT product_id FROM prices',one=True)['product_id']==b
assert db.q('SELECT merged_into FROM products WHERE id=?',(b,),one=True)['merged_into'] is None
assert not db.q('SELECT * FROM product_merges')
result=S.merge(c,canonical_id=a)
S.unmerge(result['merge_id'])
assert db.q('SELECT product_id FROM prices',one=True)['product_id']==b
''')
