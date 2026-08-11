#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from job_fetcher.config import load_config
from job_fetcher.service import classify_error
from job_fetcher.sources.factory import build_source

cid, out_path = sys.argv[1], sys.argv[2]
os.environ.setdefault('JOB_FETCHER_RETRIES','0')
os.environ.setdefault('JOB_FETCHER_HTTP_TIMEOUT','2')
os.environ.setdefault('JOB_FETCHER_DISABLE_BROWSER','1')
c = next(x for x in load_config()['companies'] if x['id']==cid)
try:
    jobs = build_source(c).fetch(c)
    row={'rank':c.get('rank'),'id':cid,'name':c['name'],'career_url':c['career_url'],'status':'success','jobs_detected':len(jobs),'category':None,'error':None}
except Exception as e:
    row={'rank':c.get('rank'),'id':cid,'name':c['name'],'career_url':c['career_url'],'status':'failed','jobs_detected':0,'category':classify_error(e),'error':f'{type(e).__name__}: {e}'}
Path(out_path).write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
