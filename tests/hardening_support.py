"""Run integration probes in a new process, isolated DB and index root. Never production."""
import os, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def probe(code):
    with tempfile.TemporaryDirectory(prefix='pricehub-hardening-') as td:
        env = dict(os.environ, PRICEHUB_DB=td+'/test.db', PRICEHUB_EMB_CACHE=td+'/emb.npz',
                   PRICEHUB_FAISS_DIR=td+'/indexes', PRICEHUB_NO_ST='1', PRICEHUB_NO_CLIP='1',
                   PRICEHUB_WARM_ST='0', PRICEHUB_QUEUE='memory', PRICEHUB_ONBOARDED='1', PYTHONPATH=str(ROOT), OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        env.pop('PRICEHUB_DB_URL',None)
        r = subprocess.run([sys.executable,'-c',code],env=env,cwd=ROOT,capture_output=True,text=True,timeout=180)
        assert r.returncode == 0, r.stdout+'\n'+r.stderr
