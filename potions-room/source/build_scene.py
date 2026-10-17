import bpy, bmesh, math, random, os, json, time
import sys
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from math import sin,cos,pi
random.seed(19)
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
M={}; parts={}
def mat(name,color,tex=None,emission=0):
 m=bpy.data.materials.new(name);m.use_nodes=True
 n=m.node_tree.nodes; n.clear();o=n.new('ShaderNodeOutputMaterial');s=n.new('ShaderNodeBsdfPrincipled');m.node_tree.links.new(s.outputs['BSDF'],o.inputs['Surface'])
 s.inputs['Base Color'].default_value=(*color,1);s.inputs['Roughness'].default_value=.75
 if tex:
  t=n.new('ShaderNodeTexImage');t.image=bpy.data.images.load(ROOT+'/textures/'+tex+'.jpg',check_existing=True);m.node_tree.links.new(t.outputs['Color'],s.inputs['Base Color'])
 m['color']=list(color);m['tex']=tex or '';m['glow']=emission;M[name]=m;parts[name]=[[],[],[]]
 return name
wood=mat('Aged golden oak',(.4,.2,.075),'oak');stone=mat('Warm limestone',(.35,.31,.25),'stone');dark=mat('Iron and shadow',(.045,.055,.054));bronze=mat('Antique brass',(.43,.26,.075));copper=mat('Hammered copper',(.49,.20,.07));paper=mat('Parchment',(.64,.49,.29));rug=mat('Woven garnet rug',(1,1,1),'rug');booktex=mat('Illustrated herbal pages',(1,1,1),'book');label=mat('Printed apothecary labels',(1,1,1),'label')
greens=[mat('Herbs '+str(i),c) for i,c in enumerate([(.12,.20,.065),(.20,.27,.08),(.25,.20,.08)])]
leathers=[mat('Book leather '+str(i),c) for i,c in enumerate([(.19,.055,.035),(.07,.12,.10),(.16,.10,.035),(.09,.065,.15)])]
potions=[mat('Glazed potion '+str(i),c) for i,c in enumerate([(.025,.31,.19),(.03,.17,.38),(.35,.055,.30),(.55,.28,.025),(.10,.39,.33),(.40,.07,.035)])]
flame=mat('Amber lamplight',(1,.47,.075),emission=1);core=mat('Golden flame',(1,.81,.35),emission=1);brew=mat('Luminous jade brew',(.04,.80,.43),emission=1);sky=mat('Blue evening glass',(.11,.24,.47),emission=1)
def mesh(name,v,f,m,uv=None):
 vv,ff,uu=parts[m];off=len(vv);vv.extend(v);ff.extend([tuple(i+off for i in face) for face in f]);
 if uv is None:
  for face in f:
   a,b,c=[Vector(v[i]) for i in face[:3]];no=(b-a).cross(c-a);axis=max(range(3),key=lambda k:abs(no[k]));axes=[k for k in range(3) if k!=axis]
   for i in face:uu.append((v[i][axes[0]]*.8,v[i][axes[1]]*.8))
 else:uu.extend(uv)
# Subdivided cuboids permit smooth vertex-baked lighting on larger surfaces.
def box(name,p,d,m,rot=0,step=.38):
 v=[];f=[];u=[]
 # face basis chosen with outward winding
 for ax,sgn in [(0,-1),(0,1),(1,-1),(1,1),(2,-1),(2,1)]:
  b=(ax+1)%3;c=(ax+2)%3; nb=max(1,math.ceil(d[b]/step));nc=max(1,math.ceil(d[c]/step));start=len(v)
  for j in range(nc+1):
   for i in range(nb+1):
    pt=[0,0,0];pt[ax]=sgn*d[ax]/2;pt[b]=(i/nb-.5)*d[b];pt[c]=(j/nc-.5)*d[c]
    xx,yy=pt[0],pt[1];pt[0]=xx*cos(rot)-yy*sin(rot)+p[0];pt[1]=xx*sin(rot)+yy*cos(rot)+p[1];pt[2]+=p[2];v.append(pt)
  for j in range(nc):
   for i in range(nb):
    a=start+j*(nb+1)+i;q=(a,a+1,a+nb+2,a+nb+1);f.append(q if sgn==1 else q[::-1])
 mesh(name,v,f,m)
def lathe(name,p,profile,m,n=20):
 v=[];f=[];uv=[]
 for z,r in profile:
  for i in range(n):v.append((p[0]+r*cos(i*2*pi/n),p[1]+r*sin(i*2*pi/n),p[2]+z))
 for j in range(len(profile)-1):
  for i in range(n):
   k=(i+1)%n;f.append((j*n+i,j*n+k,(j+1)*n+k,(j+1)*n+i));uv.extend([(i/n,j/(len(profile)-1)),((i+1)/n,j/(len(profile)-1)),((i+1)/n,(j+1)/(len(profile)-1)),(i/n,(j+1)/(len(profile)-1))])
 f.extend([tuple(range(n-1,-1,-1)),tuple((len(profile)-1)*n+i for i in range(n))]);uv.extend([(0,0)]*n+[(0,0)]*n);mesh(name,v,f,m,uv)
def tube(name,points,r,m,n=8):
 v=[];f=[]
 for j,p in enumerate(points):
  tangent=Vector(points[min(j+1,len(points)-1)])-Vector(points[max(0,j-1)]);tangent.normalize();a=tangent.cross(Vector((0,0,1)))
  if a.length<.01:a=tangent.cross(Vector((0,1,0)))
  a.normalize();b=tangent.cross(a)
  for i in range(n):v.append(Vector(p)+r*(cos(2*pi*i/n)*a+sin(2*pi*i/n)*b))
 for j in range(len(points)-1):
  for i in range(n):f.append((j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i))
 mesh(name,v,f,m)
def ring(name,p,r,th,m,axis='z',n=32):
 pts=[]
 for i in range(n+1):
  a=2*pi*i/n; q=(r*cos(a),r*sin(a),0) if axis=='z' else ((r*cos(a),0,r*sin(a)) if axis=='y' else (0,r*cos(a),r*sin(a)))
  pts.append(tuple(p[k]+q[k] for k in range(3)))
 tube(name,pts,th,m,8)
def sphere(name,p,s,m,n=16,rings=10):
 prof=[(s[2]*sin(-pi/2+pi*j/rings),s[0]*max(.001,cos(-pi/2+pi*j/rings))) for j in range(rings+1)];lathe(name,p,prof,m,n)
def panel(name,coords,m):mesh(name,coords,[(0,1,2,3)],m,[(0,0),(1,0),(1,1),(0,1)])
def bottle(x,y,z,h=.32,r=.085,m=None):
 m=m or random.choice(potions);shape=random.choice([0,1,2]);r*=random.uniform(.8,1.15)
 profile=([(0,r*.68),(.035*h,r),(.52*h,r),(.70*h,r*.48),(.76*h,r*.32),(.94*h,r*.32),(h,r*.40)] if shape==0 else [(0,r*.6),(.1*h,r),(.45*h,r*1.12),(.65*h,r*.68),(.72*h,r*.27),(.95*h,r*.27),(h,r*.39)])
 lathe('Potion bottle',(x,y,z),profile,m,16)
 lathe('Cork',(x,y,z+h),[(0,r*.28),(.05,r*.31)],paper,12)
 ring('Bottle lip',(x,y,z+h*.96),r*.35,.009,bronze,n=16)
 # front-facing parchment strip around bottle, readable at close range
 if random.random()<.67:
  v=[]
  for zz in [.19*h,.48*h]:
   for j in range(7):
    a=-pi/2+(-.65+1.3*j/6);v.append((x+r*1.02*cos(a),y+r*1.02*sin(a),z+zz))
  f=[(i,i+1,i+8,i+7) for i in range(6)];uv=[]
  for i in range(6):uv.extend([(i/6,0),((i+1)/6,0),((i+1)/6,1),(i/6,1)])
  mesh('Curved label',v,f,label,uv)
 # small baked highlight ridge, like reflective glazed glass
 tube('Glass highlight',[(x-r*.45,y-r*.9,z+.16*h),(x-r*.48,y-r*.92,z+.41*h)],.004,random.choice([bronze,m]),6)
def closedbook(x,y,z,w=.23,h=.32,rot=0):
 m=random.choice(leathers)
 box('Book page block',(x,y,z+h/2),(w,.17,h-.018),paper,rot)
 for a in [-1,1]:box('Leather cover',(x+a*w/2,y,z+h/2),(.018,.19,h+.02),m,rot)
 box('Leather spine',(x,y-.092,z+h/2),(w,.025,h+.02),m,rot)
 for a in [.12,.23,.75,.87]:box('Gilded binding',(x,y-.108,z+h*a),(w,.008,.012),bronze,rot)
def shelf(cx,cy,width,rot=0):
 def trans(x,y,z):return (cx+x*cos(rot)-y*sin(rot),cy+x*sin(rot)+y*cos(rot),z)
 for x in [-width/2,width/2]:box('Carved bookcase upright',trans(x,0,1.6),(.14,.44,3.15),wood,rot)
 box('Bookcase backing',trans(0,.18,1.6),(width,.09,3.12),wood,rot)
 for z in [.15,.85,1.45,2.05,2.65,3.2]:
  box('Shelf plank',trans(0,0,z),(width+.18,.55,.09),wood,rot)
  box('Shelf gilt edging',trans(0,-.285,z),(width+.19,.025,.024),bronze,rot)
 for row,z in enumerate([.2,.9,1.5,2.1,2.7]):
  x=-width/2+.18
  while x<width/2-.1:
   p=trans(x,random.uniform(-.08,.03),z)
   if row==0 and random.random()<.5:closedbook(*p,w=.12,h=random.uniform(.30,.48),rot=rot)
   else:bottle(*p,h=random.uniform(.24,.43),r=random.uniform(.055,.09))
   x+=random.uniform(.18,.25)
# Architectural shell; +Y is the hearth end, Z up. GlTF exporter converts to Y-up.
box('Mortar floor',(0,0,-.10),(7.8,8.6,.2),dark)
for j in range(12):
 for i in range(11):
  x=-3.6+i*.72+(j%2)*.18;y=-3.95+j*.70
  if x<3.85:box('Individual flagstone',(x,y,-.025+random.uniform(-.005,.005)),(.695,.675,.06),stone,step=.3)
# walls built of individually offset blocks
for row in range(9):
 z=.21+row*.43
 for side in [-1,1]:
  for j in range(11):
   y=-4.05+j*.78+(row%2)*.38
   if y>4.3:continue
   # real window opening on right
   if side==1 and -.55<y<2.4 and 1.3<z<3.4:continue
   box('Sidewall masonry',(side*3.85,y,z),(.25,.75,.405),stone,step=.32)
 for j in range(10):
  x=-3.5+j*.78+(row%2)*.36
  if x>3.85:continue
  box('Backwall masonry',(x,4.22,z),(.75,.25,.405),stone,step=.32)
  if abs(x)>.82 or z>2.6:box('Entry wall masonry',(x,-4.22,z),(.75,.25,.405),stone,step=.32)
# Planked ceiling and heavy rafters
for j in range(21):box('Ceiling plank',(0,-4.0+j*.4,3.94),(7.8,.385,.12),wood)
for y in [-3.6,-1.8,0,1.8,3.6]:
 box('Ceiling crossbeam',(0,y,3.74),(7.9,.22,.32),wood)
 for x in [-3.65,3.65]:box('Timber pilaster',(x,y,1.85),(.20,.23,3.7),wood)
# solid entrance door visible when turning around
for i in range(7):box('Door plank',(-.72+i*.24,-4.25,1.25),(.23,.13,2.5),wood)
for z in [.35,1.2,2.2]:box('Door iron strap',(0,-4.16,z),(1.55,.025,.065),dark)
ring('Door pull',(.49,-4.12,1.15),.085,.014,bronze,axis='y')
# window recess, emissive evening panes, diagonal lead tracery
panel('Evening window',[(3.93,-.65,1.3),(3.93,2.5,1.3),(3.93,2.5,3.45),(3.93,-.65,3.45)],sky)
for y in [-.65,.925,2.5]:box('Window stone jamb',(3.73,y,2.35),(.26,.10,2.25),stone)
for z in [1.3,3.45]:box('Window sill',(3.65,.925,z),(.5,3.35,.14),stone)
for j in range(8):
 for slope in [-1,1]:
  y0=-.65+j*.45;y1=y0+slope*1.25
  if -.65<=y1<=2.5:tube('Diamond window lead',[(3.72,y0,1.4),(3.72,y1,3.35)],.012,dark)
# Hearth stone legs and voussoir arch
for x in [-1.08,1.08]:
 for z in [.25,.65,1.05,1.45]:box('Hearth block',(x,3.72,z),(.43,.8,.38),stone,step=.2)
for j in range(13):
 a=j*pi/12;x=1.06*cos(a);z=1.48+1.06*sin(a)
 # arch blocks as oriented wedge extrusion
 a0=a-.125;a1=a+.125;v=[]
 for y in [3.27,4.12]:
  for r,aa in [(1.0,a0),(1.36,a0),(1.36,a1),(1.0,a1)]:v.append((r*cos(aa),y,1.48+r*sin(aa)))
 mesh('Arch keystone',v,[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],stone)
box('Chimney breast',(0,3.94,3.2),(2.7,.6,1.45),stone)
box('Mantel beam',(0,3.52,2.95),(3.1,1,.19),wood)
box('Hearth stone platform',(0,3.5,.12),(2.8,1.45,.24),stone)
box('Soot black fireback',(0,4.06,1.15),(1.85,.08,2.1),dark)
for i in range(5):tube('Firewood',[(-.65,3.25+i*.14,.31),(.65,3.5+i*.06,.34)],.08,wood,10)
for i in range(13):
 x=random.uniform(-.63,.63);y=random.uniform(3.25,3.9);h=random.uniform(.18,.4)
 lathe('Sculpted flame',(x,y,.35),[(0,.08),(.09,.10),(h*.7,.045),(h,.002)],flame,8)
lathe('Cauldron',(0,3.52,.62),[(0,.34),(.1,.48),(.3,.57),(.55,.56),(.68,.45),(.71,.46),(.73,.41),(.67,.4)],dark,40)
ring('Cauldron rim',(0,3.52,1.33),.45,.035,bronze)
lathe('Potion surface',(0,3.52,1.29),[(0,.408),(.006,.408)],brew,40)
for i in range(12):
 a=random.random()*2*pi;r=random.random()*.32;sphere('Brew bubble',(r*cos(a),3.52+r*sin(a),1.30),(.02,.02,.02),brew,10,6)
for x in [-.57,.57]:ring('Cauldron handle',(x,3.52,1.12),.12,.021,dark,axis='y',n=20)
# Mantel ornaments and gold celestial plaque
box('Heraldic plaque',(0,3.58,3.45),(1.15,.05,.74),leathers[1])
ring('Alchemical sun',(0,3.535,3.45),.24,.009,bronze,axis='y')
for i in range(16):
 a=i*pi/8;tube('Sunray',[(.28*cos(a),3.535,3.45+.28*sin(a)),(.34*cos(a),3.535,3.45+.34*sin(a))],.006,bronze,6)
for x in [-1.2,-.95,.95,1.2]:bottle(x,3.5,3.05,.27,.065)
# shelves flanking fireplace and along left wall
shelf(-2.55,3.85,1.6);shelf(2.55,3.85,1.6)
for y in [-2.7,-.65,1.45]:shelf(-3.53,y,1.75,pi/2)
# Workbench under window
box('Window bench top',(3.08,.8,1.02),(1.13,3.8,.13),wood)
for y in [-.8,2.4]:
 for x in [2.66,3.49]:box('Bench leg',(x,y,.48),(.14,.14,.96),wood)
box('Bench lower shelf',(3.08,.8,.24),(1.04,3.6,.09),wood)
# Center table plank top and legs; generous circulation
for j in range(6):box('Table top plank',(-1.35+j*.54,-.55,1.04),(.525,1.50,.14),wood,step=.2)
for x in [-1.30,1.30]:
 for y in [-1.13,.03]:
  box('Table turned leg',(x,y,.51),(.17,.17,1.02),wood)
  for z in [.15,.8]:box('Leg carved collar',(x,y,z),(.22,.22,.08),wood)
box('Table under-shelf',(0,-.55,.25),(2.8,1.4,.10),wood)
for y in [-1.22,.12]:
 box('Table apron',(0,y,.87),(2.9,.1,.25),wood)
 for x in [-.96,0,.96]:
  box('Drawer panel',(x,y-.065,.88),(.86,.035,.18),wood)
  ring('Drawer pull',(x,y-.09,.88),.04,.009,bronze,axis='y',n=16)
# Carpet with detailed original woven pattern
panel('Persian carpet',[(-1.98,-1.92,.015),(1.98,-1.92,.015),(1.98,.89,.015),(-1.98,.89,.015)],rug)
# table centerpiece open book, covers and wedge page blocks
box('Open book cover',(-.25,-.72,1.15),(.86,.58,.055),leathers[0])
panel('Open herbarium left',[(-.67,-1.0,1.195),(-.25,-1.0,1.17),(-.25,-.44,1.17),(-.67,-.44,1.195)],booktex)
# assign UV manually spread halves
parts[booktex][2][-4:]=[(0,0),(.5,0),(.5,1),(0,1)]
panel('Open herbarium right',[(-.25,-1,1.17),(.17,-1,1.195),(.17,-.44,1.195),(-.25,-.44,1.17)],booktex);parts[booktex][2][-4:]=[(.5,0),(1,0),(1,1),(.5,1)]
for x,y,h,r in [(-1.15,-.3,.48,.105),(-.85,-.18,.34,.09),(-.52,-.15,.40,.08),(.7,-.38,.29,.095),(1.1,-.9,.24,.07)]:bottle(x,y,1.12,h,r)
# mortar and pestle
lathe('Stone mortar',(.76,-.9,1.12),[(0,.13),(.07,.17),(.21,.21),(.24,.21),(.24,.17),(.1,.11)],stone,28)
tube('Pestle',[(.77,-.9,1.22),(.98,-.87,1.55)],.035,stone,12)
# Brass balance, cups, fine chains
lathe('Balance foot',(.55,.0,1.12),[(0,.14),(.04,.14),(.07,.055)],bronze)
tube('Balance upright',[(.55,0,1.16),(.55,0,1.80)],.018,bronze)
tube('Balance arm',[(.20,0,1.74),(.90,0,1.74)],.015,bronze)
for x in [.22,.88]:
 for dx in [-.10,.10]:tube('Balance chain',[(x,0,1.74),(x+dx,0,1.44)],.004,bronze,5)
 lathe('Balance dish',(x,0,1.40),[(0,.025),(.04,.13),(.05,.13)],bronze)
# tabletop rolled parchments and books under table
for j in range(3):tube('Rolled parchment',[(1.05,-.20+j*.12,1.17),(1.45,-.20+j*.12,1.17)],.04,paper,12)
for i in range(7):closedbook(-1.10+i*.18,-.70,.31,.15,.38)
# Distillation apparatus on side counter: bulb, cap, spout, coiled condenser
lathe('Copper alembic',(3.0,.8,1.10),[(0,.23),(.1,.28),(.3,.31),(.46,.25),(.51,.06),(.67,.05)],copper,32)
ring('Alembic seam',(3,.8,1.4),.30,.018,bronze)
tube('Swan neck',[(3,.8,1.77),(3,.8,2.05),(3,.65,2.20),(3,.32,2.18),(3,-.05,1.95)],.035,copper,12)
pts=[]
for i in range(181):
 a=i/180*10*pi;pts.append((3+.15*cos(a),-.10+.15*sin(a),1.92-i/180*.60))
tube('Condenser spiral',pts,.017,copper,8)
bottle(3,-.1,1.10,.21,.12,potions[0])
for y in [1.6,1.9,2.2]:bottle(2.9,y,1.1,.3,.08)
# Baskets, barrels and lidded ceramic jars
for x,y in [(2.9,-2.7),(-2.8,-3.5),(3,2.65)]:
 lathe('Oak barrel',(x,y,.06),[(0,.32),(.12,.35),(.45,.39),(.8,.35),(.88,.32)],wood,24)
 for z in [.18,.52,.84]:ring('Barrel hoop',(x,y,z),.36 if z!=.52 else .39,.022,dark,n=24)
 for j in range(12):
  a=j*pi/6;tube('Barrel stave joint',[(x+.34*cos(a),y+.34*sin(a),.12),(x+.39*cos(a),y+.39*sin(a),.47),(x+.34*cos(a),y+.34*sin(a),.87)],.004,dark,5)
for x,y,z in [(.8,-.5,.31),(1.2,-.4,.31),(3.1,1.8,.3),(3.1,.9,.3)]:
 lathe('Storage jar',(x,y,z),[(0,.13),(.08,.18),(.28,.19),(.36,.13),(.4,.14)],paper,20);lathe('Jar lid',(x,y,z+.4),[(0,.16),(.035,.16),(.06,.04)],wood,20)
# Three-legged stools
for x,y in [(1.95,-1.8),(2.25,1.6)]:
 lathe('Stool seat',(x,y,.57),[(0,.25),(.07,.25)],wood,24)
 for a in [0,2*pi/3,4*pi/3]:tube('Stool leg',[(x+.23*cos(a),y+.23*sin(a),.05),(x+.17*cos(a),y+.17*sin(a),.58)],.035,wood,8)
# Herb bundles hanging along beams
for x,y in [(-2.5,2.8),(2.6,2.7),(3.45,1.8),(-3.1,-1.8),(2.9,-2.5),(-1.6,3.1)]:
 tube('Hanging twine',[(x,y,3.70),(x,y,3.1)],.008,paper,5)
 for j in range(13):
  dx=random.uniform(-.14,.14);dy=random.uniform(-.12,.12);bottom=random.uniform(2.65,2.95)
  tube('Herb stem',[(x,y,3.15),(x+dx,y+dy,bottom)],.005,greens[0],5)
  for k in range(4):
   zz=bottom+k*.07;xx=x+dx;yy=y+dy
   mesh('Dried herb leaves',[(xx,yy,zz),(xx-.07,yy-.02,zz+.03),(xx-.035,yy+.02,zz+.08),(xx+.065,yy+.02,zz+.03)],[(0,1,2),(0,2,3)],random.choice(greens))
# Lantern cages and candle flames
lights=[]
def lantern(x,y,z):
 tube('Lantern suspension',[(x,y,z+.40),(x,y,3.73)],.012,dark,8)
 lathe('Lantern base',(x,y,z-.21),[(0,.15),(.05,.18),(.07,.14)],bronze,6)
 lathe('Lantern roof',(x,y,z+.24),[(0,.19),(.12,.06)],bronze,6)
 for j in range(6):
  a=j*pi/3;tube('Lantern cage',[(x+.15*cos(a),y+.15*sin(a),z-.15),(x+.15*cos(a),y+.15*sin(a),z+.26)],.012,bronze,6)
 lathe('Candle',(x,y,z-.14),[(0,.045),(.23,.045)],paper,12)
 lathe('Candle flame',(x,y,z+.09),[(0,.035),(.05,.04),(.15,.001)],core,10)
 lights.append(((x,y,z+.1),(1,.57,.22),1.2))
for x,y,z in [(-2.6,-2.7,2.9),(-2.7,1.5,2.85),(0,-1.0,2.93),(2.6,2.65,2.9),(2.8,-2.8,2.95)]:lantern(x,y,z)
lights.extend([((0,3.05,.55),(1,.42,.12),3.5),((0,3.5,1.6),(.12,1,.62),.45),((3.4,.85,2.6),(.35,.55,1),2.8)])
# Assemble material-batched geometry: one draw call per material.
objects=[]
for name,(v,f,uv) in parts.items():
 if not v:continue
 me=bpy.data.meshes.new(name);me.from_pydata(v,[],f);me.update();ob=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(ob);me.materials.append(M[name]);objects.append(ob)
 layer=me.uv_layers.new(name='UVMap')
 for i,q in enumerate(uv):layer.data[i].uv=q
 print(name,len(v),len(f),flush=True)
# Join face vertices and add small modeled edge bevels for close VR inspection.
for ob in objects:
 if ob.name in ['Aged golden oak','Warm limestone']:
  bpy.context.view_layer.objects.active=ob
  bm=bmesh.new();bm.from_mesh(ob.data);bmesh.ops.remove_doubles(bm,verts=list(bm.verts),dist=.00001);bm.to_mesh(ob.data);bm.free()
  mod=ob.modifiers.new('Worn edges','BEVEL');mod.width=.012;mod.segments=2;mod.limit_method='ANGLE'
  bpy.ops.object.modifier_apply(modifier=mod.name)
 if ob.name.startswith('Glazed potion') or ob.name in ['Hammered copper','Antique brass','Iron and shadow']:
  for poly in ob.data.polygons:poly.use_smooth=True
# Offline vertex illumination. Occlusion rays make shelves, joints and recesses read clearly,
# without any per-frame shadows or runtime lights in the WebXR renderer.
verts=[];faces=[]
for ob in objects:
 if ob.active_material['glow']:continue
 offset=len(verts);verts.extend([v.co.copy() for v in ob.data.vertices]);faces.extend([tuple(offset+i for i in p.vertices) for p in ob.data.polygons])
bvh=BVHTree.FromPolygons(verts,faces)
raydirs=[Vector(q).normalized() for q in [(1,1,1),(-1,1,1),(1,-1,1),(-1,-1,1),(.3,.8,.2),(-.6,.2,.6)]]
for ob in objects:
 me=ob.data;col=me.color_attributes.new(name='BakedLight',type='FLOAT_COLOR',domain='CORNER');me.color_attributes.active_color=col;glow=ob.active_material['glow'];cache={}
 for face in me.polygons:
  n=face.normal
  for li in face.loop_indices:
   vi=me.loops[li].vertex_index
   n=me.vertices[vi].normal if face.use_smooth else face.normal
   key=(vi,tuple(round(t,2) for t in n))
   if key not in cache:
    p=me.vertices[vi].co;origin=p+n*.012
    if glow:rgb=[1,1,1]
    else:
     hits=0;count=0
     for d0 in raydirs:
      d=d0 if d0.dot(n)>0 else -d0
      if d.dot(n)<.1:continue
      count+=1;hit=bvh.ray_cast(origin,d,.65)
      if hit[0] is not None:hits+=1
     ao=1-.65*hits/max(1,count)
     rgb=[.13*ao,.105*ao,.075*ao]
     for lp,lc,power in lights:
      delta=Vector(lp)-p;dist=delta.length;direction=delta.normalized();nd=max(0,n.dot(direction))
      if nd<=0:continue
      hit=bvh.ray_cast(origin,direction,max(.01,dist-.04))
      visibility=1 if hit[0] is None else .12
      fall=power/(.7+dist*dist)*nd*visibility
      for k in range(3):rgb[k]+=lc[k]*fall
      if ob.name.startswith('Glazed potion') or ob.name in ['Hammered copper','Antique brass']:
       view=(Vector((2.45,-3.75,1.9))-p).normalized();half=(view+direction).normalized();spec=max(0,n.dot(half))**48*visibility*.9/(.8+dist*.25)
       for k in range(3):rgb[k]+=lc[k]*spec
     rgb=[min(.98,max(.03,c)) for c in rgb]
    cache[key]=(*rgb,1)
   col.data[li].color=cache[key]
 print('Lit',ob.name,flush=True)
# In Blender multiply baked vertex radiance by tiled albedo; glTF exports the same
# product through standard COLOR_0 and KHR_materials_unlit.
for m in M.values():
 n=m.node_tree.nodes;n.clear();out=n.new('ShaderNodeOutputMaterial');em=n.new('ShaderNodeEmission');m.node_tree.links.new(em.outputs[0],out.inputs['Surface'])
 vc=n.new('ShaderNodeVertexColor');vc.layer_name='BakedLight';mix=n.new('ShaderNodeMixRGB');mix.blend_type='MULTIPLY';mix.inputs[0].default_value=1;m.node_tree.links.new(vc.outputs['Color'],mix.inputs[2]);m.node_tree.links.new(mix.outputs[0],em.inputs[0])
 if m['tex']:
  tx=n.new('ShaderNodeTexImage');tx.image=bpy.data.images.load(ROOT+'/textures/'+m['tex']+'.jpg',check_existing=True);m.node_tree.links.new(tx.outputs['Color'],mix.inputs[1])
 else:mix.inputs[1].default_value=(*m['color'],1)
# Camera for exact asset preview
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=16;scene.cycles.use_denoising=False
scene.world.color=(.04,.04,.04)
bpy.ops.object.camera_add(location=(2.45,-3.75,1.9));cam=bpy.context.object;cam.name='Preview camera';direction=Vector((-.15,1.5,1.6))-cam.location;cam.rotation_euler=direction.to_track_quat('-Z','Y').to_euler();cam.data.lens=20;scene.camera=cam
scene.render.resolution_x=2048;scene.render.resolution_y=1408;scene.render.resolution_percentage=100
scene.view_settings.view_transform='Standard';scene.view_settings.look='Medium High Contrast' if 'Medium High Contrast' in [x.name for x in bpy.types.ColorManagedViewSettings.bl_rna.properties['look'].enum_items] else 'None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=ROOT+'/source/potions-room.blend')
scene.render.filepath=ROOT+'/preview.png';bpy.ops.render.render(write_still=True)
# Replace shader trees for uncomplicated glTF unlit export; vertex color multiplication
# is intrinsic to glTF and therefore does not need to appear in this export-only shader.
for m in M.values():
 n=m.node_tree.nodes;n.clear();out=n.new('ShaderNodeOutputMaterial');em=n.new('ShaderNodeEmission');m.node_tree.links.new(em.outputs[0],out.inputs['Surface'])
 if m['tex']:
  tx=n.new('ShaderNodeTexImage');tx.image=bpy.data.images.load(ROOT+'/textures/'+m['tex']+'.jpg',check_existing=True);m.node_tree.links.new(tx.outputs['Color'],em.inputs[0])
 else:em.inputs[0].default_value=(*m['color'],1)
for ob in bpy.context.selected_objects:ob.select_set(False)
for ob in objects:ob.select_set(True)
bpy.context.view_layer.objects.active=objects[0]
bpy.ops.export_scene.gltf(filepath=ROOT+'/potions-room.gltf',export_format='GLTF_SEPARATE',use_selection=True,export_apply=True,export_texcoords=True,export_normals=True,export_colors=True,export_materials='EXPORT',export_yup=True,export_extras=True)
print('EXPORT COMPLETE',flush=True)
