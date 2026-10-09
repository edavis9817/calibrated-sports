import json, sys, os, sqlite3, numpy as np
sys.path.insert(0,"D:/temp/f32/c-39src"); os.chdir("D:/temp/f32/c-39src")
from models import cfb_game as C
from research import cfb_game_forecast as CF
gm=CF.grid(CF.GRID_MOV); inst=np.load("D:/temp/f32/inst.npz"); seasons=[int(s) for s in inst["seasons"]]
sums,counts,fb=inst["sums"],inst["counts"],inst["first_bad"]; ever=fb!=9999
out={}
for aval in (1.0,2.2,4.0):
    sub=np.array([p.a==aval for p in gm]); d=[]
    for T in range(2005,2027):
        def ch(mask):
            s=sums.copy(); s[:,mask|~sub]=np.nan
            return C.best_params(gm,seasons,s,counts,CF.FIT_FROM,T)
        pw,lw,_=ch(ever); pa,la,_=ch(fb<T)
        if pa!=pw: d.append((T,int(fb[gm.index(pa)]),round(la,5),round(lw,5),pa.as_dict(),pw.as_dict()))
    print("a=%s: %d points, %d ever bad (%.1f%%); seasons where the as-of choice differs from the whole-walk choice: %d"%(aval,sub.sum(),(ever&sub).sum(),100*(ever&sub).sum()/sub.sum(),len(d)))
    for x in d: print("   ",x)
    out[str(aval)]={"points":int(sub.sum()),"ever_bad":int((ever&sub).sum()),"differ":d}
json.dump(out,open("D:/temp/f32/explore_a1.json","w"),indent=1)
