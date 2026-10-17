"""Photo-quality shaded isometric for the bottom-right recognition view.

TechDraw only draws line views and FreeCAD's 3D viewer does not exist in
console mode, so this module is a small software renderer:

* per-face tessellation with **true B-rep surface normals** at every vertex,
  so cylinders and fillets shade smoothly (Phong, per-pixel normals);
* a depth-tested deferred z-buffer, rendered 2-3x larger and downsampled
  (supersampled anti-aliasing);
* three-point lighting - key, fill, rim - plus Blinn-Phong specular on a
  satin material and gentle tone mapping;
* a soft ground contact shadow under the part;
* TechDraw's own hidden-line-removed edges of the same view drawn on top,
  anti-aliased ("shaded with edges").

Output is a transparent PNG placed as ``TechDraw::DrawViewImage``. Identical
result in the GUI and headless.
"""

import os
import tempfile

import FreeCAD as App

from . import geometry as G

DEFAULT_COLOR = (0x6f, 0x9f, 0xc8)  # steel blue
EDGE_WIDTH_MM = 0.25
PX_PER_MM = 12.0  # ~300 dpi on paper
MAX_SUPER_PIXELS = 9_000_000


def part_color(obj):
    """Colour of the recognition render.

    Always the full-colour default (project rule); FreeCAD's body colours are
    mostly the grey default and would make the view look unshaded. Pass
    ``iso_color`` to create_drawing for a different colour.
    """
    return DEFAULT_COLOR


def _qt():
    from .render import _qt as q

    return q()


def _mesh(shape, deflection):
    """Vertices, smooth vertex normals (from the surfaces) and triangles."""
    V, N, T = [], [], []
    for face in shape.Faces:
        try:
            pts, tris = face.tessellate(deflection)
        except Exception:
            continue
        if not tris:
            continue
        base = len(V)
        surf = face.Surface
        planar = type(surf).__name__ == "Plane"
        fn = None
        if planar:
            try:
                u0, u1, v0, v1 = face.ParameterRange
                fn = face.normalAt(0.5 * (u0 + u1), 0.5 * (v0 + v1))
            except Exception:
                fn = None
        for p in pts:
            V.append((p.x, p.y, p.z))
            n = fn
            if n is None:
                try:
                    u, v = surf.parameter(p)
                    n = face.normalAt(u, v)
                except Exception:
                    n = App.Vector(0, 0, 0)
            N.append((n.x, n.y, n.z))
        for a, b, c in tris:
            T.append((base + a, base + b, base + c))
    return V, N, T


def _box_blur(a, r):
    import numpy as np

    if r < 1:
        return a
    for axis in (0, 1):
        c = np.cumsum(np.pad(a, [(r + 1, r) if i == axis else (0, 0) for i in range(2)], mode="edge"), axis=axis)
        if axis == 0:
            a = (c[2 * r + 1:, :] - c[:-2 * r - 1, :]) / (2 * r + 1)
        else:
            a = (c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)
    return a


def _raster(P, Z, T, W, H, attrs=None):
    """Depth-tested rasterisation. Returns zbuf and interpolated attrs (H, W, k)."""
    import numpy as np

    zbuf = np.full((H, W), -np.inf, dtype=np.float32)
    out = np.zeros((H, W, attrs.shape[1]), dtype=np.float32) if attrs is not None else None
    for a, b, c in T:
        xa, ya = P[a]; xb, yb = P[b]; xc, yc = P[c]
        x0 = max(int(min(xa, xb, xc)), 0); x1 = min(int(max(xa, xb, xc)) + 1, W - 1)
        y0 = max(int(min(ya, yb, yc)), 0); y1 = min(int(max(ya, yb, yc)) + 1, H - 1)
        if x1 < x0 or y1 < y0:
            continue
        den = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
        if abs(den) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1, dtype=np.float32) + 0.5, np.arange(y0, y1 + 1, dtype=np.float32) + 0.5)
        w0 = ((yb - yc) * (gx - xc) + (xc - xb) * (gy - yc)) / den
        w1 = ((yc - ya) * (gx - xc) + (xa - xc) * (gy - yc)) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-5) & (w1 >= -1e-5) & (w2 >= -1e-5)
        if not inside.any():
            continue
        z = w0 * Z[a] + w1 * Z[b] + w2 * Z[c]
        sub = zbuf[y0:y1 + 1, x0:x1 + 1]
        upd = inside & (z > sub)
        if not upd.any():
            continue
        sub[upd] = z[upd]
        if out is not None:
            vals = w0[upd, None] * attrs[a] + w1[upd, None] * attrs[b] + w2[upd, None] * attrs[c]
            out[y0:y1 + 1, x0:x1 + 1][upd] = vals
    return zbuf, out


def render_iso_png(shape, iso_view, scale, color=DEFAULT_COLOR, path=None, ppm=PX_PER_MM):
    """Render ``shape`` as seen by ``iso_view`` at paper ``scale``.

    Returns ``(path, width_mm, height_mm, local_bbox)``.
    """
    import numpy as np

    QtCore, QtGui, _ = _qt()
    d = App.Vector(iso_view.Direction); d.normalize()
    x = App.Vector(iso_view.XDirection); x.normalize()
    up = G.up_vector(d, x)

    vis = G.visibility_map(iso_view)
    polylines = [G.discretize(e, 0.3) for i, e in G.iter_edges(iso_view) if vis.get(i, True)]
    lb = G.view_local_bbox(iso_view)
    if lb is None:
        raise RuntimeError("isometric view has no geometry")

    bb = shape.BoundBox
    diag = bb.DiagonalLength
    V, N, T = _mesh(shape, max(0.005, diag / 900.0))
    if len(T) > 150000:
        V, N, T = _mesh(shape, diag / 250.0)
    V = np.array(V, dtype=np.float64)
    N = np.array(N, dtype=np.float64)
    T = np.array(T, dtype=np.int64)
    X = np.array([x.x, x.y, x.z]); U = np.array([up.x, up.y, up.z]); D = np.array([d.x, d.y, d.z])

    pu, pv, pz = V @ X, V @ U, V @ D
    off_u = 0.5 * (lb[0] + lb[2]) - 0.5 * (pu.min() + pu.max())
    off_v = 0.5 * (lb[1] + lb[3]) - 0.5 * (pv.min() + pv.max())

    margin = 1.0
    shadow_room = 0.06 * (lb[3] - lb[1]) * scale + 1.5
    w_mm = (lb[2] - lb[0]) * scale + 2 * margin
    h_mm = (lb[3] - lb[1]) * scale + 2 * margin + shadow_room
    W, H = max(8, int(w_mm * ppm)), max(8, int(h_mm * ppm))
    ss = 3 if W * H * 9 <= MAX_SUPER_PIXELS else 2 if W * H * 4 <= MAX_SUPER_PIXELS else 1
    Ws, Hs = W * ss, H * ss

    def px(u, v, k=1.0):
        return (((u - lb[0]) * scale + margin) * ppm * k, (h_mm - ((v - lb[1]) * scale + margin + shadow_room)) * ppm * k)

    Px, Py = px(pu + off_u, pv + off_v, ss)
    P = np.stack([Px, Py], axis=1)

    # --- ground contact shadow: footprint dropped onto the floor plane -------
    floor = bb.ZMin
    Vg = V.copy(); Vg[:, 2] = floor
    gx, gy = px(Vg @ X + off_u, Vg @ U + off_v, 1.0)  # blurred anyway: render at 1x
    zs, _ = _raster(np.stack([gx, gy], axis=1), np.zeros(len(Vg)), T, W, H)
    shadow = np.isfinite(zs).astype(np.float32)
    shadow = _box_blur(_box_blur(shadow, max(1, int(0.9 * ppm))), max(1, int(0.6 * ppm)))
    if ss > 1:
        shadow = np.repeat(np.repeat(shadow, ss, axis=0), ss, axis=1)

    # --- deferred G-buffer: depth + smooth normal --------------------------------
    zbuf, nbuf = _raster(P, pz, T, Ws, Hs, N.astype(np.float32))
    mask = np.isfinite(zbuf)
    n = nbuf
    ln = np.linalg.norm(n, axis=2, keepdims=True)
    n = np.where(ln > 1e-6, n / np.maximum(ln, 1e-6), 0.0)
    view = D.astype(np.float32)
    facing = n @ view
    n = np.where(facing[..., None] < 0, -n, n)  # two-sided (bore walls, inner faces)
    facing = np.abs(facing)

    def nl(v):
        v = np.asarray(v, dtype=np.float32)
        return v / np.linalg.norm(v)

    key = nl(0.55 * D + 0.65 * U - 0.45 * X)
    fill = nl(0.75 * D - 0.25 * U + 0.65 * X)
    rim = nl(-0.6 * D + 0.5 * U + 0.2 * X)
    half = nl(key + view)
    base = np.array(color, dtype=np.float32) / 255.0
    diff_k = np.clip(n @ key, 0, 1)
    diff_f = np.clip(n @ fill, 0, 1)
    spec = np.clip(n @ half, 0, 1) ** 48
    rim_t = np.clip(n @ rim, 0, 1) * (1 - facing) ** 2
    light = 0.30 + 0.62 * diff_k + 0.22 * diff_f
    rgb = base[None, None, :] * light[..., None] + 0.38 * spec[..., None] + 0.20 * rim_t[..., None]
    # subtle depth cue: far geometry slightly darker
    if mask.any():
        zmin, zmax = float(zbuf[mask].min()), float(zbuf[mask].max())
        depth = (zbuf - zmin) / max(zmax - zmin, 1e-6)
        rgb *= (0.90 + 0.10 * np.where(mask, depth, 1.0))[..., None]
    rgb = 1.0 - np.exp(-1.6 * rgb)  # filmic-ish tone map
    rgb /= 1.0 - np.exp(-1.6)

    alpha = mask.astype(np.float32)
    sh_a = 0.32 * shadow * (1 - alpha)
    out = np.zeros((Hs, Ws, 4), dtype=np.float32)
    out[..., :3] = np.where(mask[..., None], rgb, 0.0)
    out[..., 3] = alpha + sh_a
    # downsample (supersampling AA), premultiplied
    if ss > 1:
        prem = out[..., :3] * alpha[..., None]
        prem = prem.reshape(H, ss, W, ss, 3).mean(axis=(1, 3))
        a = alpha.reshape(H, ss, W, ss).mean(axis=(1, 3))
        a_tot = out[..., 3].reshape(H, ss, W, ss).mean(axis=(1, 3))
    else:
        prem = out[..., :3] * alpha[..., None]
        a = alpha
        a_tot = out[..., 3]
    col = np.where(a[..., None] > 1e-4, prem / np.maximum(a[..., None], 1e-4), 0.0)
    # composite shadow (black) under the part colour
    final_rgb = col * (a / np.maximum(a_tot, 1e-4))[..., None]
    rgba = np.zeros((H, W, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(final_rgb * 255.0, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(a_tot * 255.0, 0, 255).astype(np.uint8)

    img = QtGui.QImage(rgba.tobytes(), W, H, 4 * W, QtGui.QImage.Format_RGBA8888).copy()
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(25, 28, 32), EDGE_WIDTH_MM * ppm)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.NoBrush)
    for pl in polylines:
        if len(pl) < 2:
            continue
        p.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(*px(u, v)) for u, v in pl]))
    p.end()
    if path is None:
        fd, path = tempfile.mkstemp(suffix="_iso.png")
        os.close(fd)
    img.save(path, "PNG")
    return path, w_mm, h_mm, lb


def place_shaded_iso(doc, page, shape, iso_view, scale, center_xy, color=DEFAULT_COLOR, name="IsometricShaded"):
    """Replace the line iso view by a shaded image view centred in its zone."""
    path, w_mm, h_mm, _ = render_iso_png(shape, iso_view, scale, color)
    img = doc.addObject("TechDraw::DrawViewImage", name)
    page.addView(img)
    img.ImageFile = path
    try:
        img.ImageIncluded = path  # embed a copy in the .FCStd so the view survives the temp file
    except Exception:
        pass
    img.Width = w_mm
    img.Height = h_mm
    img.X, img.Y = center_xy
    img.Caption = ""
    for prop, typ, val in (("FDScale", "App::PropertyFloat", float(scale)),
                           ("FDDirection", "App::PropertyVector", App.Vector(iso_view.Direction)),
                           ("FDXDirection", "App::PropertyVector", App.Vector(iso_view.XDirection))):
        if prop not in img.PropertiesList:
            img.addProperty(typ, prop, "FreeDrawing", "shaded isometric metadata")
        setattr(img, prop, val)
    try:
        os.remove(path)
    except OSError:
        pass
    return img, w_mm, h_mm
