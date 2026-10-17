from PIL import Image,ImageDraw,ImageFont,ImageFilter
import numpy as np, os
out=os.path.join(os.path.dirname(__file__),'..','textures');os.makedirs(out,exist_ok=True)
rng=np.random.default_rng(32)
S=2048
y,x=np.mgrid[0:S,0:S]/S
# Multi-scale seamless noise, generated locally, no external texture licenses.
def noise():
 a=np.zeros((S,S),np.float32)
 for n,w in [(8,.40),(32,.25),(128,.2),(512,.1),(2048,.05)]:
  ar=(rng.random((n,n))*255).astype('uint8');a+=np.array(Image.fromarray(ar).resize((S,S),Image.Resampling.BICUBIC))*w/255
 return a
n=noise()
def save(name,a):Image.fromarray(np.uint8(np.clip(a,0,255))).save(os.path.join(out,name+'.jpg'),quality=94)
grain=(np.sin(y*380+np.sin(x*24)*3+np.sin(x*100)*.7)+np.sin(y*1350+np.sin(x*12)*8))*.075
v=.58+n*.55+grain
save('oak',np.stack([v*116,v*72,v*37],-1))
v=.55+n*.75+np.sin(x*50+y*60+n*12)*.03
save('stone',np.stack([v*143,v*136,v*121],-1))
# Inlaid floral carpet with decorative borders.
im=Image.new('RGB',(S,S),(65,24,26));d=ImageDraw.Draw(im)
for inset,col,width in [(22,(145,107,54),8),(45,(46,49,48),38),(78,(170,131,68),8),(101,(88,64,38),24),(128,(171,126,61),6),(172,(134,84,43),5)]:d.rectangle((inset,inset,S-inset,S-inset),outline=col,width=width)
for yy in range(200,1900,145):
 for xx in range(200,1900,145):
  col=(133,91,47) if (xx+yy)%2 else (120,106,62)
  d.polygon([(xx,yy-43),(xx+24,yy),(xx,yy+43),(xx-24,yy)],outline=col,width=4)
  d.ellipse((xx-8,yy-8,xx+8,yy+8),fill=(179,131,68))
for r in range(440,80,-35):
 d.polygon([(1024,1024-r),(1024+r*.7,1024),(1024,1024+r),(1024-r*.7,1024)],outline=(168,119,62),width=9)
a=np.array(im).astype(float)*(0.8+n[...,None]*.35);save('rug',a)
# An original illustrated recipe spread.
im=Image.new('RGB',(2048,1024),(207,181,126));d=ImageDraw.Draw(im)
font='/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf'
f=ImageFont.truetype(font,46);small=ImageFont.truetype(font,24)
d.rectangle((20,20,2028,1004),outline=(106,70,39),width=4)
d.line((1024,0,1024,1024),fill=(103,74,43),width=24)
d.text((130,95),'HERBARIUM NOCTIS',font=f,fill=(72,47,31));d.text((1170,95),'THE LANTERN DRAUGHT',font=ImageFont.truetype(font,32),fill=(72,47,31))
for xx in [270,650]:
 d.line((xx,820,xx+25,290),fill=(76,90,45),width=9)
 for j in range(8):
  yy=330+j*55
  d.ellipse((xx-110,yy-20,xx+10,yy+22),fill=(91,106,58),outline=(61,73,41),width=3)
  d.ellipse((xx+10,yy+12,xx+120,yy+54),fill=(111,118,66),outline=(61,73,41),width=3)
for j in range(18):
 yy=230+j*35
 for k in range(8):
  xx=1150+k*90
  d.line((xx,yy,xx+int(rng.integers(35,75)),yy+int(rng.integers(-3,4))),fill=(94,66,37),width=3)
d.ellipse((1430,780,1590,940),outline=(115,68,39),width=4);d.polygon([(1510,790),(1440,910),(1580,910)],outline=(115,68,39),width=4)
im.save(out+'/book.jpg',quality=95)
# Bottle label atlas is tiled on selected curved bands.
im=Image.new('RGB',(512,512),(191,166,111));d=ImageDraw.Draw(im)
d.rectangle((20,20,492,492),outline=(86,57,30),width=8);d.rectangle((35,35,477,477),outline=(107,75,35),width=3)
d.ellipse((170,100,340,270),outline=(78,63,36),width=9);d.polygon([(255,115),(180,245),(330,245)],outline=(78,63,36),width=6)
d.text((110,310),'ELIXIR',font=ImageFont.truetype(font,58),fill=(66,42,25));d.text((150,395),'No.  XVII',font=ImageFont.truetype(font,30),fill=(66,42,25));im.save(out+'/label.jpg',quality=95)
print('Textures generated')
