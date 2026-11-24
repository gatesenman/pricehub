import pytest
from app.core.scheduler import Scheduler,TokenBucket
from app.core import tenancy

def test_handler_outside_retry_policy_does_not_kill_worker():
    s=Scheduler(workers=1,rate=1000,burst=1000)
    s.register_handler('bad',lambda p:(_ for _ in ()).throw(ValueError('unexpected')),retry_on=(TimeoutError,))
    s.register_handler('good',lambda p:'ok')
    try:
        bad=s.submit('bad'); good=s.submit('good')
        assert s.wait([bad,good],5)
        assert bad.state=='dead' and good.result=='ok'
        assert s.threads[0].is_alive()
    finally: s.stop()

def test_concurrent_submit_and_tenant_context():
    from concurrent.futures import ThreadPoolExecutor
    s=Scheduler(workers=2,rate=10000,burst=10000)
    s.register_handler('ws',lambda p:tenancy.current())
    def submit(i):
        with tenancy.use(i): return s.submit('ws')
    try:
        with ThreadPoolExecutor(8) as pool: jobs=list(pool.map(submit,range(1,33)))
        assert s.wait(jobs,5)
        assert [j.result for j in jobs]==list(range(1,33))
        assert len(s.threads)==2
    finally: s.stop()

@pytest.mark.parametrize('rate,burst',[(0,2),(-1,2),(float('inf'),2),(1,0),(1,float('nan'))])
def test_invalid_token_buckets(rate,burst):
    with pytest.raises(ValueError): TokenBucket(rate,burst)
