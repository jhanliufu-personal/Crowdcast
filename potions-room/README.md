# The Lantern Apothecary

A complete, walkable potion-room scene inspired by the approved concept image. This is a stylized, modeled interpretation, not a photogrammetric reconstruction or a promise of the reference image's photorealistic detail.

## Files

- `potions-room.gltf` — requested glTF 2.0 scene. Keep `potions-room.bin` and the five root-level JPG textures beside it.
- `potions-room.glb` — equivalent single-file binary glTF, including all geometry and textures. Convenient for importing into other tools.
- `index.html` — self-contained local-dependency WebXR viewer. The included `vendor/` folder contains Three.js 0.180.0 and its MIT license. No CDN is needed at runtime.
- `preview.png` — Blender render of the same modeled scene.
- `source/potions-room.blend` — editable Blender scene with baked vertex lighting. Geometry is combined by material for efficient rendering; props are not individually selectable nodes. In Blender, Separate > By Loose Parts can separate disconnected geometry if needed.
- `source/build_scene.py`, `source/textures.py`, `source/package_gltf.py` — deterministic generation and packaging scripts.
- `scene-stats.json`, `*.validation.json`, `browser-check.json` — asset metrics and validation results.

## Open on a Meta Quest, without a PC rendering connection

1. Extract the ZIP.
2. Serve the extracted `potions-room` folder from an **HTTPS static web host**, preserving its file layout. Upload the whole folder, not only the HTML or `.gltf` file. No build step or backend is required. You can omit `source/`, previews, the `.glb`, and validation reports when hosting the viewer.
3. Open the hosted `index.html` URL in Meta Quest Browser.
4. Wait for the room to load and select **ENTER VR**. Grant the browser's VR permission if requested.
5. Aim either controller downward at an open area of the floor. A green ring indicates an allowed destination. Press the trigger to teleport. Move the right thumbstick horizontally for 30-degree snap turns.

The headset renders the scene locally. Hosting only delivers files and does not require a PC connection during use. This package is not already deployed to a public URL. A `file://` URL is not a substitute for HTTPS WebXR hosting. For desktop inspection, `python3 -m http.server 8000` from the extracted folder and `http://localhost:8000` are sufficient; a headset visiting a plain HTTP LAN address generally cannot enter immersive WebXR.

Desktop: drag to orbit, right-drag to pan, scroll to zoom. The three viewpoint buttons give quick views of the entrance, workbench and hearth.

## Scene and rendering details

- Units: meters. glTF axes: Y up. Room approximately 7.8 m wide, 8.5 m deep and 4 m high.
- Suggested XR rig spawn: `(0, 0, 3.35)`, facing negative Z. Use `local-floor` and the headset's measured eye height.
- 266,340 triangles and 26 material batches; no runtime shadow maps, transparency passes, skeletal animation or postprocessing.
- Three 2048×2048 textures (oak, limestone, rug), one 2048×1024 illustrated book, one 512×512 label texture. Textures are original procedural/drawn assets.
- glTF uses `KHR_materials_unlit` and `COLOR_0` to preserve baked illumination. The loader must support that extension and vertex colors. The bundled viewer does.
- Lighting, reflections, fire and potion surfaces are static. Bottles use opaque colored surfaces with baked highlights, rather than physically refractive glass. Props cannot be picked up in this version.
- The viewer adds teleport destinations and snap turning. glTF alone does not contain WebXR controls or collision logic. Its conservative destination bounds exclude furniture, but are not a physics simulation. Physical room-scale motion is not collision-constrained; follow the headset boundary.
- The viewer uses the headset's native WebXR frame rate, a framebuffer scale of 1, no tone mapping, and at most 4× texture anisotropy.

## Verification and limits

The `.gltf` and `.glb` were checked with the Khronos glTF validator: zero errors and zero warnings. Informational unused-UV notices may remain for solid-color materials. The browser test checks loading, JavaScript errors, visible rendering and desktop view buttons. An actual Quest was not available: immersive session startup, controller behavior and headset frame rate remain unverified. Performance varies by Quest model and browser. The scene is designed for standalone use, but no minimum FPS is claimed.

## Rebuild

Use Python 3 with NumPy and Pillow for `source/textures.py`, then Blender 4.x in background mode for `source/build_scene.py`, followed by Python 3 for `source/package_gltf.py`. Run from the extracted folder:

```sh
python3 source/textures.py
blender --background --threads 6 --python source/build_scene.py
python3 source/package_gltf.py
```

The build script creates geometry, UVs and baked vertex illumination, renders the preview, and exports the separate glTF. The packaging script converts Blender's emission export to standard unlit materials (preserving vertex-light multiplication) and builds the GLB. Rebuilding overwrites generated scene assets. The Blender source embeds its textures for portability.

Three.js references: https://threejs.org/docs/pages/GLTFLoader.html and https://threejs.org/docs/pages/VRButton.html
