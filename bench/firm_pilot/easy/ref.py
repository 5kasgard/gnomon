import re, datetime, heapq
from fractions import Fraction
from collections import OrderedDict

def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def _to_roman(n):
    out=""
    for v,sym in [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]:
        while n>=v: out+=sym; n-=v
    return out
_R={_to_roman(i):i for i in range(1,4000)}
def roman_to_int(s):
    if s not in _R: raise ValueError(s)
    return _R[s]

def merge_intervals(xs):
    out=[]
    for a,b in sorted(xs):
        if out and a<=out[-1][1]: out[-1]=(out[-1][0],max(out[-1][1],b))
        else: out.append((a,b))
    return out

class LRUCache:
    def __init__(self,c): self.c=c; self.d=OrderedDict()
    def get(self,k):
        if k not in self.d: return -1
        self.d.move_to_end(k); return self.d[k]
    def put(self,k,v):
        if self.c<=0: return
        if k in self.d: self.d.move_to_end(k)
        self.d[k]=v
        if len(self.d)>self.c: self.d.popitem(last=False)

def evaluate(s):
    toks=re.findall(r"\s*(\d+|\*\*|[-+*/()]|\S)",s)
    if "".join(t.strip() for t in toks)!=re.sub(r"\s","",s): raise ValueError("bad")
    toks=[t.strip() for t in toks]; i=0
    def peek(): return toks[i] if i<len(toks) else None
    def eat():
        nonlocal i; t=peek(); i+=1; return t
    def add():
        v=mul()
        while peek() in ("+","-"):
            op=eat(); r=mul(); v=v+r if op=="+" else v-r
        return v
    def mul():
        v=unary()
        while peek() in ("*","/"):
            op=eat(); r=unary()
            if op=="*": v=v*r
            else:
                if r==0: raise ValueError("div0")
                v=v/r
        return v
    def unary():
        if peek() in ("+","-"):
            op=eat(); v=unary(); return v if op=="+" else -v
        return power()
    def power():
        b=atom()
        if peek()=="**":
            eat(); e=unary()
            if e.denominator!=1: raise ValueError("exp")
            if b==0 and e<0: raise ValueError("div0")
            return b**int(e)
        return b
    def atom():
        t=eat()
        if t is None: raise ValueError("eof")
        if t=="(":
            v=add()
            if eat()!=")": raise ValueError(")")
            return v
        if t.isdigit(): return Fraction(int(t))
        raise ValueError(t)
    v=add()
    if peek() is not None: raise ValueError("trailing")
    return v

def glob_match(pattern,path):
    ps=pattern.split("/"); xs=path.split("/") if path!="" else []
    if pattern=="": return path==""
    import functools
    def seg(p,x):
        return re.fullmatch("".join("[^/]*" if c=="*" else "[^/]" if c=="?" else re.escape(c) for c in p),x) is not None
    @functools.lru_cache(None)
    def m(i,j):
        if i==len(ps): return j==len(xs)
        if ps[i]=="**":
            return any(m(i+1,k) for k in range(j,len(xs)+1))
        return j<len(xs) and seg(ps[i],xs[j]) and m(i+1,j+1)
    return m(0,0)

def topo_sort(deps):
    nodes=set(deps)|{d for ds in deps.values() for d in ds}
    indeg={n:0 for n in nodes}; out={n:[] for n in nodes}
    for n,ds in deps.items():
        for d in set(ds): indeg[n]+=1; out[d].append(n)
    h=[n for n in nodes if indeg[n]==0]; heapq.heapify(h); res=[]
    while h:
        n=heapq.heappop(h); res.append(n)
        for m in out[n]:
            indeg[m]-=1
            if indeg[m]==0: heapq.heappush(h,m)
    if len(res)!=len(nodes): raise ValueError("cycle")
    return res

def business_days(start,end,holidays):
    if end<start: return -business_days(end,start,holidays)
    def wd_before(d):  # weekdays in [epoch, d)
        n=d.toordinal()-1  # ordinal 1 is Monday 0001-01-01
        w,r=divmod(n,7)
        return w*5+min(r,5)
    c=wd_before(end)-wd_before(start)
    c-=sum(1 for h in holidays if start<=h<end and h.weekday()<5)
    return c

def canonical_json(v):
    def s(x):
        out=['"']
        for ch in x:
            o=ord(ch)
            if ch=='"': out.append('\\"')
            elif ch=='\\': out.append('\\\\')
            elif ch=='\b': out.append('\\b')
            elif ch=='\f': out.append('\\f')
            elif ch=='\n': out.append('\\n')
            elif ch=='\r': out.append('\\r')
            elif ch=='\t': out.append('\\t')
            elif o<0x20: out.append('\\u%04x'%o)
            else: out.append(ch)
        out.append('"'); return "".join(out)
    if v is None: return "null"
    if v is True: return "true"
    if v is False: return "false"
    if isinstance(v,int): return str(v)
    if isinstance(v,str): return s(v)
    if isinstance(v,list): return "["+",".join(canonical_json(x) for x in v)+"]"
    if isinstance(v,dict):
        ks=sorted(v,key=lambda k:k.encode("utf-16-be"))
        return "{"+",".join(s(k)+":"+canonical_json(v[k]) for k in ks)+"}"
    raise TypeError

def multiply(a,b):
    return str(int(a)*int(b))

def shortest_path(graph,src,dst):
    h=[(0,[src])]; best={}
    while h:
        c,p=heapq.heappop(h); n=p[-1]
        if n in best: continue
        best[n]=(c,p)
        if n==dst: return (c,p)
        for m,w in graph.get(n,[]):
            if m not in best: heapq.heappush(h,(c+w,p+[m]))
    return None

def wrap(text,width):
    paras=[]; cur=[]
    for line in text.split("\n"):
        if line.strip()=="":
            if cur: paras.append(cur); cur=[]
        else: cur+=line.split()
    if cur: paras.append(cur)
    out=[]
    for i,words in enumerate(paras):
        if i: out.append("")
        line=""
        for w in words:
            if len(w)>width:
                if line: out.append(line); line=""
                for k in range(0,len(w),width): out.append(w[k:k+width])
                continue
            if not line: line=w
            elif len(line)+1+len(w)<=width: line+=" "+w
            else: out.append(line); line=w
        if line: out.append(line)
    return out
