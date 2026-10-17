# The Hall of the Verdant Egg

Refined version: layered nave arcades and vault moldings, carved capital details, wall niches, stained-glass rose windows, a crimson embroidered runner, smoother stone textures, and stronger warm/cool baked illumination. The central dais has been enlarged; teleportation excludes its footprint.

A modeled interpretation of the approved castle concept for standalone WebXR: a 22 × 35 metre hall with vaulted ceilings rising about 17 metres, galleries, banners, chandeliers, and a central ornamental egg.

## Files

- `castle-hall.gltf` with its adjacent `.bin` and JPG files: complete scene, including the egg.
- `castle-hall.glb`: the same scene with geometry and textures embedded in one file.
- `precious-egg.gltf` / `.glb`: separate reusable egg and cradle; do not load this alongside the hall unless a second egg is wanted.
- `index.html` and `vendor/`: self-contained viewer dependencies, desktop orbit controls, and WebXR teleportation / snap turning.
- `source/castle-hall.blend`: editable Blender source, with packed textures. Other source scripts reproduce the scene and procedural textures.
- `preview.png`, `egg-preview.png`: rendered previews.

## Rendering and budget

The complete hall has 277,510 triangles, 108 spatial/material batches, 15 materials and 8 textures. The separate egg has 26,968 triangles and 3 batches. The hall GLB is approximately 27.3 MB. Texture resolutions are predominantly 2048 × 2048, with a 1024 × 2048 banner.

All surfaces are opaque and use `KHR_materials_unlit`, texture colour and baked vertex illumination. Stone and cloth are matte; gold uses baked colour variation rather than live gloss. No dynamic shadows, reflections, bloom or volumetric effects are required. Windows are opaque stained-glass textures, and candle flames are static opaque shapes. Geometry is grouped by bay and material to support frustum culling. Lighting is fixed and will not react to moving objects. The egg is a static exhibit, without animation or physics.

## Launch

For desktop preview, serve this folder with a static HTTP server and open its index page. To enter VR on a standalone headset, host the folder on HTTPS, open that URL in the headset browser, and choose Enter VR. Opening a downloaded HTML file directly is insufficient. No remote CDN dependencies are used.

In VR, aim either controller at a clear part of the floor and press its trigger to teleport. Use the right thumbstick horizontally for 30-degree snap turns. Teleport destinations exclude the pedestal, column footprints, benches and wall edges. This is a destination filter, not a physical collision system; real-world walking remains governed by the headset boundary. Initial floor position is `[0, 0, 9]` in glTF coordinates. Units are metres, with Y up.

## Validation

Both scene formats and both egg formats pass the Khronos glTF validator with zero errors and warnings. The desktop Chromium viewer loads and renders successfully. The package includes validation and browser-check reports. No physical Quest performance or controller testing has been performed; final frame rate, thermal behaviour and texture memory should be assessed on the target headset. Desktop draw counts are not a stereo headset performance measurement.

Original geometry and textures were created for this scene. The bundled Three.js dependencies retain their MIT license in `vendor/LICENSE`.

To regenerate: run `source/make_textures.py`, then `source/refine_textures.py` with Python/Pillow/NumPy, then run `source/build_hall.py` in Blender, followed by `source/package_scene.py` in Python.
