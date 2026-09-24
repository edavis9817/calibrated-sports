import json,sys,collections
import sys
for st in sys.argv[1:] or ['s1','s2']:
    rows=[json.loads(l) for l in open(f'out-{st}/{st}.jsonl',encoding='utf8')]
    by=collections.defaultdict(list)
    for r in rows: by[r['route']].append(r)
    print(f'================ {st}')
    for route in sorted(by, key=lambda x:(x.count('/analytics/'),x.count('/team/'),x)):
        rs=by[route]
        if '/analytics/' in route and route.count('/')==3 and len(rs)==1: continue
        if '/team/' in route and len(rs)==1: continue
        st_=sorted({r.get('status') for r in rs}, key=str)
        def u(f): return sorted({x for r in rs for x in (r.get(f) or [])})
        print(f"{route} status={st_} n={len(rs)} textLen={min(r.get('textLen',0) for r in rs)}-{max(r.get('textLen',0) for r in rs)} wide={max(r.get('wide',0) or 0 for r in rs)} ph={max(r.get('placeholderHtml',0) for r in rs)} err={[r.get('error') for r in rs if r.get('error')][:1]} ground={sorted({str(r.get('dataGround')) for r in rs})}")
        for f in ['pending','marks','bareChips','errorStates','awaiting','loading','stale','banned','data404','pageErr','consoleErr']:
            v=u(f)
            if v: print(f"   {f}: {v[:8]}")
        if any(r.get('nextError') or r.get('notFoundText') for r in rs): print('   NOTFOUND/ERR text', rs[0].get('h1'), rs[0].get('title'))
