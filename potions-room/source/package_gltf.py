import json,os,struct,copy
from pathlib import Path
root=Path(__file__).resolve().parent.parent
p=root/'potions-room.gltf';d=json.loads(p.read_text())
# Blender's pure Emission export becomes emissive PBR. Convert it to glTF unlit
# albedo so COLOR_0 correctly multiplies the albedo in every standards-based loader.
for m in d['materials']:
 if 'emissiveFactor' in m:
  pbr={'baseColorFactor':m.pop('emissiveFactor')+[1], 'metallicFactor':0,'roughnessFactor':1}
  if 'emissiveTexture' in m:pbr['baseColorTexture']=m.pop('emissiveTexture')
  m['pbrMetallicRoughness']=pbr
  m.setdefault('extensions',{})['KHR_materials_unlit']={}
 m.pop('extras',None)
d['extensionsUsed']=['KHR_materials_unlit'];d['extensionsRequired']=['KHR_materials_unlit']
d.setdefault('asset',{})['copyright']='Original scene and textures created for the user.'
d['asset']['extras']={'units':'meters','upAxis':'Y','lighting':'Offline baked vertex illumination; no runtime lights required','suggestedSpawn':[0,0,3.35]}
p.write_text(json.dumps(d,separators=(',',':')))
# A single-file GLB companion removes external-path management for integrations.
g=copy.deepcopy(d);binary=bytearray((root/g['buffers'][0]['uri']).read_bytes());g['buffers'][0].pop('uri')
for im in g['images']:
 uri=im.pop('uri');data=(root/uri).read_bytes()
 while len(binary)%4:binary.append(0)
 view={'buffer':0,'byteOffset':len(binary),'byteLength':len(data)};g['bufferViews'].append(view);im['bufferView']=len(g['bufferViews'])-1;im['mimeType']='image/jpeg';binary.extend(data)
g['buffers'][0]['byteLength']=len(binary)
while len(binary)%4:binary.append(0)
j=json.dumps(g,separators=(',',':')).encode();j+=b' '*((-len(j))%4)
(root/'potions-room.glb').write_bytes(struct.pack('<4sII',b'glTF',2,12+8+len(j)+8+len(binary))+struct.pack('<I4s',len(j),b'JSON')+j+struct.pack('<I4s',len(binary),b'BIN\x00')+binary)
stats={'triangles':sum(d['accessors'][pr['indices']]['count']//3 for m in d['meshes'] for pr in m['primitives']),'materialBatches':len(d['meshes']),'materials':len(d['materials']),'gltfBytes':p.stat().st_size,'glbBytes':(root/'potions-room.glb').stat().st_size,'textureDimensions':{'oak':[2048,2048],'stone':[2048,2048],'rug':[2048,2048],'book':[2048,1024],'label':[512,512]}}
(root/'scene-stats.json').write_text(json.dumps(stats,indent=2));print(stats)
