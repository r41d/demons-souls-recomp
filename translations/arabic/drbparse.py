import struct
def parse(d):
    o=0x10; secs={}
    while o+16<=len(d):
        tag=d[o:o+4][::-1].decode('latin1').strip('\0'); size,count=struct.unpack_from('>II',d,o+4)
        secs[tag]=(o+16,size,count); o=(o+16+size+15)&~15
        if tag=='END': break
    str0=secs['STR'][0]; shpr0=secs['SHPR'][0]; shap0,_,n=secs['SHAP']
    def s(off):
        e=str0+off
        while d[e:e+2]!=b'\0\0': e+=2
        return d[str0+off:e].decode('utf-16-be')
    shapes=[]
    for i in range(n):
        t,po=struct.unpack_from('>II',d,shap0+i*8)
        shapes.append(dict(i=i,type=s(t),off=shpr0+po))
    return secs,shapes,s
