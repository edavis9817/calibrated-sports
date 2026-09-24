import sys
from PIL import Image
for f in sys.argv[1:]:
    im=Image.open(f); w,h=im.size; step=1800 if w>500 else 2200
    n=0
    for y in range(0,h,step):
        c=im.crop((0,y,w,min(h,y+step)))
        if w>500: c=c.resize((int(w*0.7),int(c.size[1]*0.7)))
        name=f"tiles/{f.split('/')[0]}-{f.split('/')[-1][:-4]}-{n}.png"; c.save(name); n+=1
    print(f,h,n)
