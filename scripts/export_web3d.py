#!/usr/bin/env python3
"""把裁切好的 DEM 导出成 three.js 能直接渲染的三维地形（离线、单目录、双击即看）。

产出目录:
  <out>/index.html                    三维查看器（拖拽旋转、悬停查高程、垂直夸张）
  <out>/vendor/                       three.js 本地副本，不依赖 CDN
  <out>/data/terrain_h.bin            顶点高程（float32，米，无效处 NaN）
  <out>/data/terrain_idx.bin          三角形索引（uint32，只连有效格元，天然挖出边界形状）
  <out>/data/terrain_meta.json        尺寸 / 像元 / 经纬度范围 / 高程极值 / 面积
  <out>/start.command | start.bat     起本地静态服务并打开浏览器

用法:
  python export_web3d.py 紫阳县_dem.tif                 # 输出到 ./紫阳县_dem_web3d
  python export_web3d.py 紫阳县_dem.tif -o out3d --name 紫阳县
  python export_web3d.py 紫阳县_dem.tif --target 900    # 更精细（面数更多，文件更大）

为什么必须起本地服务: 页面用 fetch 读二进制，file:// 协议会被浏览器拦掉。

注意:
  - DEM 是 DSM，含植被与建筑，城区三维会有细碎毛刺，属数据本身特性。
  - 垂直夸张默认 ×5：1 是真实比例（山区会显得很平），8~10 很适合出图。
  - 索引只连四角都有效的格元，所以边界外自动镂空，不会出现贴地的一张方片。
"""
import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import rasterio

HERE = Path(__file__).resolve().parent
VIEWER = HERE.parent / "web3d"


def nan_box_blur(a, mask, iters=2):
    """3×3 NaN 安全均值平滑：只对有效像元求平均，避免边界被拉黑。"""
    H, W = a.shape
    s = np.where(mask, a, 0.0).astype(np.float64)
    c = mask.astype(np.float64)
    for _ in range(iters):
        ps, pc = np.pad(s, 1), np.pad(c, 1)
        ss, cc = np.zeros_like(s), np.zeros_like(c)
        for di in range(3):
            for dj in range(3):
                ss += ps[di:di + H, dj:dj + W]
                cc += pc[di:di + H, dj:dj + W]
        with np.errstate(invalid="ignore", divide="ignore"):
            s = np.where(cc > 0, ss / np.maximum(cc, 1e-9), 0.0)
        c = (cc > 0).astype(np.float64)
    return s, c


def build(src, out, name=None, target=620, smooth=2):
    out = Path(out)
    (out / "data").mkdir(parents=True, exist_ok=True)

    with rasterio.open(src) as ds:
        arr = ds.read(1).astype(np.float64)
        if ds.nodata is not None and not math.isnan(ds.nodata):
            arr = np.where(arr == ds.nodata, np.nan, arr)
        pxs, pys = ds.transform.a, -ds.transform.e
        bounds = list(ds.bounds)

    mask = np.isfinite(arr)
    if not mask.any():
        raise SystemExit("这份 DEM 没有有效像元，先检查 nodata 设置")

    # 抽稀到目标格网：按 stride 分块求均值（同时起到抗锯齿作用）
    H, W = arr.shape
    stride = max(1, math.ceil(max(H, W) / target))
    Hs, Ws = (H // stride) * stride, (W // stride) * stride
    a = arr[:Hs, :Ws].reshape(Hs // stride, stride, Ws // stride, stride)
    m = mask[:Hs, :Ws].reshape(Hs // stride, stride, Ws // stride, stride)
    with np.errstate(invalid="ignore"):
        cnt = m.sum(axis=(1, 3))
        total = np.where(m, a, 0.0).sum(axis=(1, 3))
        h = np.where(cnt > 0, total / np.maximum(cnt, 1), np.nan)
    coverage = cnt / float(stride * stride)   # 每个抽稀格的边界内占比（0~1），用于算面积

    if smooth > 0:
        h_s, h_m = nan_box_blur(h, np.isfinite(h), iters=smooth)
        h = np.where(h_m, h_s, np.nan)

    hh, ww = h.shape
    lat0 = (bounds[1] + bounds[3]) / 2.0
    lon0 = (bounds[0] + bounds[2]) / 2.0
    dx_m = pxs * 111320.0 * math.cos(math.radians(lat0)) * stride
    dz_m = pys * 110540.0 * stride

    # 面积按行积分（纬度不同经线间距不同），比用中心纬度一个常数更准
    row_lat = np.linspace(bounds[3] - pys * stride / 2,
                          bounds[1] + pys * stride / 2, hh)   # 单位已是度
    row_w_m = pxs * 111320.0 * np.cos(np.radians(row_lat)) * stride
    area_km2 = float((coverage * row_w_m[:, None]).sum() * dz_m / 1e6)

    # 四角都有效才连面 —— 边界外自然镂空
    ok = np.isfinite(h)
    q = ok[:-1, :-1] & ok[:-1, 1:] & ok[1:, 1:] & ok[1:, :-1]
    ii, jj = np.nonzero(q)
    a0 = (ii * ww + jj).astype(np.uint32)
    b0 = (ii * ww + jj + 1).astype(np.uint32)
    c0 = ((ii + 1) * ww + jj + 1).astype(np.uint32)
    d0 = ((ii + 1) * ww + jj).astype(np.uint32)
    tri = np.empty((len(ii) * 6,), dtype=np.uint32)
    tri[0::6], tri[1::6], tri[2::6] = a0, d0, b0
    tri[3::6], tri[4::6], tri[5::6] = b0, d0, c0

    name = name or Path(src).stem.replace("_dem", "")
    valid = h[np.isfinite(h)]
    cell_km2 = dx_m * dz_m / 1e6
    meta = {
        "name": name,
        "source": "Copernicus GLO-30 (DSM)",
        "grid": [int(ww), int(hh)],
        "stride": int(stride),
        "dx_km": round(dx_m / 1000.0, 4),
        "dz_km": round(dz_m / 1000.0, 4),
        "width_km": round(dx_m * (ww - 1) / 1000.0, 2),
        "height_km": round(dz_m * (hh - 1) / 1000.0, 2),
        "min_h": round(float(valid.min()), 1),
        "max_h": round(float(valid.max()), 1),
        "mean_h": round(float(valid.mean()), 1),
        "lat0": round(lat0, 5),
        "lon0": round(lon0, 5),
        "bounds": [round(b, 6) for b in bounds],
        "cell_km2": round(cell_km2, 5),
        "valid_cells": int(ok.sum()),
        "tris": int(len(tri) // 3),
        "area_km2": round(area_km2, 2),
    }

    h.astype(np.float32).tofile(out / "data" / "terrain_h.bin")
    tri.tofile(out / "data" / "terrain_idx.bin")
    (out / "data" / "terrain_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    # 复制查看器（含本地 three.js），保证离线可用
    if VIEWER.exists():
        shutil.copy2(VIEWER / "index.html", out / "index.html")
        vdir = out / "vendor"
        if not vdir.is_dir():
            vdir.mkdir(parents=True)
        for f in ("three.module.js", "OrbitControls.js"):
            if (VIEWER / "vendor" / f).exists():
                shutil.copy2(VIEWER / "vendor" / f, out / "vendor" / f)
        (out / "start.command").write_text(
            '#!/bin/bash\ncd "$(dirname "$0")"\nPORT=8099\n'
            'while lsof -i :$PORT >/dev/null 2>&1; do PORT=$((PORT+1)); done\n'
            'python3 -m http.server $PORT >/dev/null 2>&1 &\n'
            'sleep 1\nopen "http://localhost:$PORT/index.html"\n'
            'echo "服务已启动: http://localhost:$PORT/index.html  （关闭此窗口即停止）"\n'
            'wait\n', encoding="utf-8")
        os.chmod(out / "start.command", 0o755)
        (out / "start.bat").write_text(
            "@echo off\r\ncd /d %~dp0\r\nstart \"\" http://localhost:8099/index.html\r\n"
            "python -m http.server 8099\r\n", encoding="utf-8")

    size_h = (out / "data" / "terrain_h.bin").stat().st_size / 1048576
    size_i = (out / "data" / "terrain_idx.bin").stat().st_size / 1048576
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    print(f"\n输出目录: {out}")
    print(f"  terrain_h.bin   {size_h:.2f} MB")
    print(f"  terrain_idx.bin {size_i:.2f} MB")
    print(f"  面上三角面 {meta['tris']:,} 个，面积 {meta['area_km2']} km²")
    print("\n打开方式: cd 到输出目录，双击 start.command（Windows 用 start.bat）")
    print("          或手动: python3 -m http.server 8099 然后访问 http://localhost:8099/index.html")
    return meta


def main():
    ap = argparse.ArgumentParser(
        description="DEM -> three.js 三维地形（离线可看）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python export_web3d.py 紫阳县_dem.tif\n"
               "  python export_web3d.py 紫阳县_dem.tif -o out3d --target 900 --smooth 3\n")
    ap.add_argument("tif", help="裁切好的 DEM（GeoTIFF，EPSG:4326）")
    ap.add_argument("-o", "--out", help="输出目录，默认 <tif 同名>_web3d")
    ap.add_argument("--name", help="显示名称，默认取文件名去掉 _dem")
    ap.add_argument("--target", type=int, default=620,
                    help="抽稀后长边格点数，默认 620（约 60 万三角面）")
    ap.add_argument("--smooth", type=int, default=2, help="平滑迭代次数，默认 2，0 为不平滑")
    a = ap.parse_args()

    out = a.out or (str(Path(a.tif).with_suffix("")) + "_web3d")
    build(a.tif, out, a.name, a.target, a.smooth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
