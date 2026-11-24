import pytest
from app.core.price_input import amount,timestamp,observations
from hardening_support import probe

@pytest.mark.parametrize('v',[None,True,'bad',float('nan'),float('inf'),-1])
def test_invalid_price(v):
    with pytest.raises(ValueError): amount(v)

def test_forecast_filters_and_sorts():
    from app.domains.analysis.forecast_v2 import _series
    y,dates=_series([{'price':3,'ts':'2026-01-03'},{'price':float('nan')},{'price':1,'ts':'2026-01-01'}, {'price':-5}])
    assert y.tolist()==[1,3] and dates[0]<dates[1]
    assert timestamp('2026-01-02T08:00:00+08:00')=='2026-01-02 00:00:00'

def test_backfilled_price_does_not_become_latest():
    probe('''
from app import main as M, db
from fastapi import HTTPException
pid=M.add_product({'title':'history','platform':'local'})['id']
M.record_price(pid,100,'manual','2026-01-03')
M.record_price(pid,50,'manual','2026-01-01')
assert M.product(pid)['last_price']==100
assert M.products()[0]['last_price']==100
assert [r['price'] for r in M.history(pid)['history']]==[50,100]
before=len(db.q('SELECT * FROM prices'))
for val in (-1,'nan',True,None):
    try: M.record_price(pid,val,'manual')
    except HTTPException as e: assert e.status_code==400
    else: raise AssertionError('bad price accepted')
assert len(db.q('SELECT * FROM prices'))==before
try: M.record_price(99999,1,'manual')
except HTTPException as e: assert e.status_code==404
else: raise AssertionError('orphan price')
''')
