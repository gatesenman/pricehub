from hardening_support import probe

def test_currency_ranking_and_unknown_rate():
    probe('''
from app import main as M, db
from app.domains.normalize import service as N
from fastapi import HTTPException
N.set_fx({'rates':{'USD':7.0}})
a=M.upsert(None,{'title':'a','platform':'local','external_id':'a','group_key':'same','currency':'CNY','price':100})
b=M.upsert(None,{'title':'b','platform':'local','external_id':'b','group_key':'same','currency':'USD','price':20})
c=M.upsert(None,{'title':'c','platform':'local','external_id':'c','group_key':'same','currency':'XYZ','price':1})
r=M.compare(a)
assert r['cheapest']['id']==a and r['spread']==40
assert len(r['unconverted'])==1 and r['unconverted'][0]['id']==c
assert M.pricing(a)['market_median']==140
assert M.deep_compare(str(a)+','+str(b))['rank']['last_price']['best'] is None
assert M.deep_compare(str(a)+','+str(b))['currency_warning']
for rate in (0,-1,'nan','inf',True):
    try: N.set_fx({'rates':{'USD':rate}})
    except HTTPException as e: assert e.status_code==400
    else: raise AssertionError('invalid rate')
assert N.fx()['USD']==7
''')

def test_comparison_ui_uses_normalized_prices():
    from pathlib import Path
    html=Path('app/static/index.html').read_text()
    assert 'o.comparison_price ?? o.price' in html
    assert 't.d.currency_warning' in html
