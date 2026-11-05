#!/usr/bin/env python3
"""产物自检：一条命令确认这份 DEM 能不能直接进工程。

检查 8 项硬指标（缺失的辅助文件会标 SKIP，不影响结论）：

  1. 坐标系     必须是 EPSG:4326
  2. 数据类型   必须是 float32
  3. nodata     必须是 NaN（或显式声明的数值 nodata）
  4. 像元尺寸   必须是 1/3600°，并换算成米提示"非正方形"
  5. 黑边       边界外不能是 0（0 是合法高程，会被当成洼地）
  6. 高程合理性 有效值落在 -500 ~ 9000 m
  7. 面积自洽   像元累加 vs 边界多边形，偏差 < 1%（需 --boundary）
  8. 预览图     是否为 RGBA 透明底（需 --preview）

用法:
  python verify_output.py 紫阳县_dem.tif
  python verify_output.py 紫阳县_dem.tif -b 紫阳县.json
  python verify_output.py 紫阳县_dem.tif -b 紫阳县.json -p 紫阳县_dem_preview.png --json report.json

退出码: 0 全部通过 / 1 有失败项 / 2 文件读不了
"""
import argparse
import json
import math
import sys
import unicodedata

import numpy as np
import rasterio


def disp_w(s):
    """终端显示宽度：中日韩全角字符占 2 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def row(name, ok, detail):
    ok = None if ok is None else bool(ok)
    return (name, ok, detail)


def render(name, ok, detail, width=24):
    mark = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    pad = " " * max(2, width - disp_w(name))
    return f"[{mark}] {name}{pad}{detail}"


def check(tif, boundary=None, preview=None, expect_epsg=4326, max_area_dev=1.0):
    rows = []
    with rasterio.open(tif) as ds:
        crs, dt, nod, res = ds.crs, ds.dtypes[0], ds.nodata, ds.res
        arr = ds.read(1).astype(np.float64)
        H, W = arr.shape
        bounds = ds.bounds

        epsg = crs.to_epsg() if crs else None
        rows.append(row("坐标系", epsg == expect_epsg, f"EPSG:{epsg}（要求 EPSG:{expect_epsg}）"))
        rows.append(row("数据类型", dt == "float32", dt))

        nan_nodata = nod is None or (isinstance(nod, float) and math.isnan(nod))
        rows.append(row("nodata", nan_nodata,
                        "NaN（边界外透明，不会被插值成 0）" if nan_nodata
                        else f"{nod}（建议改 NaN，或确认下游软件支持该值）"))

        lat0 = (bounds.bottom + bounds.top) / 2.0
        ew = abs(res[0]) * 111320.0 * math.cos(math.radians(lat0))
        ns = abs(res[1]) * 110574.0
        is_3600 = abs(abs(res[0]) - 1 / 3600) < 1e-6 and abs(abs(res[1]) - 1 / 3600) < 1e-6
        rows.append(row("像元尺寸", is_3600,
                        f"{abs(res[0]):.7f}° ≈ {ew:.1f} m 东西 × {ns:.1f} m 南北（非正方形，面积须投影后算）"))

        n_zero = int((arr == 0).sum())
        n_nan = int(np.isnan(arr).sum())
        rows.append(row("黑边检查", n_zero == 0,
                        f"0 值像元 {n_zero} 个，NaN 占 {n_nan / arr.size * 100:.1f}%"))

        valid = arr[np.isfinite(arr)]
        if valid.size == 0:
            rows.append(row("高程合理性", False, "没有有效像元"))
            min_h = max_h = mean_h = float("nan")
        else:
            min_h, max_h, mean_h = float(valid.min()), float(valid.max()), float(valid.mean())
            rows.append(row("高程合理性", -500 < min_h and max_h < 9000,
                            f"{min_h:.0f} ~ {max_h:.0f} m，均值 {mean_h:.0f} m"))

        pix_area = float(np.isfinite(arr).sum()) * ew * ns / 1e6
        poly_area = None
        if boundary:
            try:
                from pyproj import Geod
                import shapely.geometry as sg
                gj = json.load(open(boundary, encoding="utf-8"))
                geom = gj["geometry"] if gj.get("type") == "Feature" else gj["features"][0]["geometry"]
                poly_area = abs(Geod(ellps="WGS84").geometry_area_perimeter(sg.shape(geom))[0]) / 1e6
                dev = abs(pix_area - poly_area) / poly_area * 100
                rows.append(row("面积自洽", dev < max_area_dev,
                                f"像元累加 {pix_area:.2f} km² vs 边界多边形 {poly_area:.2f} km²，偏差 {dev:.2f}%"))
            except Exception as e:  # noqa: BLE001
                rows.append(row("面积自洽", None, f"边界文件读取失败: {e}"))
        else:
            rows.append(row("面积自洽", None, f"未提供 --boundary，仅报像元面积 {pix_area:.2f} km²"))

        if preview:
            try:
                from PIL import Image
                im = Image.open(preview)
                has_alpha = im.mode == "RGBA"
                frac = 0.0
                if has_alpha:
                    frac = float((np.array(im)[..., 3] == 0).mean() * 100)
                rows.append(row("预览图透明底", has_alpha,
                                f"{im.mode} {im.size[0]}×{im.size[1]}，"
                                + (f"透明像素 {frac:.1f}%（边界外应透明）" if has_alpha
                                   else "非 RGBA，边界外会带底色")))
            except Exception as e:  # noqa: BLE001
                rows.append(row("预览图透明底", None, f"读取失败: {e}"))

        info = {
            "file": str(tif), "crs": f"EPSG:{epsg}", "dtype": dt,
            "nodata": None if nan_nodata else nod, "grid": [W, H],
            "res_deg": [abs(res[0]), abs(res[1])],
            "res_m": [round(ew, 2), round(ns, 2)],
            "bounds": [round(bounds.left, 6), round(bounds.bottom, 6),
                       round(bounds.right, 6), round(bounds.top, 6)],
            "elev": {"min": round(min_h, 1), "max": round(max_h, 1), "mean": round(mean_h, 1)},
            "valid_ratio": round(1 - n_nan / arr.size, 4),
            "pixel_area_km2": round(pix_area, 2),
            "polygon_area_km2": round(poly_area, 2) if poly_area else None,
        }
    return rows, info


def main():
    ap = argparse.ArgumentParser(
        description="DEM 产物 8 项验收自检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python verify_output.py 紫阳县_dem.tif -b 紫阳县.json\n"
               "  python verify_output.py 紫阳县_dem.tif -b 紫阳县.json --json report.json\n")
    ap.add_argument("tif", help="待检 DEM（GeoTIFF）")
    ap.add_argument("-b", "--boundary", help="边界 GeoJSON，用于面积自洽校验")
    ap.add_argument("-p", "--preview", help="预览 PNG，顺带检查是否为透明底")
    ap.add_argument("--epsg", type=int, default=4326, help="期望坐标系，默认 4326")
    ap.add_argument("--max-area-dev", type=float, default=1.0, help="面积允许偏差 %%，默认 1.0")
    ap.add_argument("--json", help="把结果写成 JSON 报告")
    a = ap.parse_args()

    try:
        rows, info = check(a.tif, a.boundary, a.preview, a.epsg, a.max_area_dev)
    except Exception as e:  # noqa: BLE001
        print(f"读不了这个文件: {e}")
        return 2

    print(f"\n验收对象: {a.tif}\n")
    for name, ok, detail in rows:
        print("  " + render(name, ok, detail))

    judged = [ok for _, ok, _ in rows if ok is not None]
    passed = sum(1 for ok in judged if ok)
    failed = len(judged) - passed
    print(f"\n共 {len(rows)} 项：通过 {passed}，失败 {failed}，"
          f"跳过 {len(rows) - len(judged)}")
    print("结论: " + ("可以直接进工程" if failed == 0 else "先修掉失败项再往下用"))

    if a.json:
        payload = {
            "checks": [{"name": n, "result": ("PASS" if ok else "FAIL") if ok is not None else "SKIP",
                        "detail": d} for n, ok, d in rows],
            "summary": {"passed": passed, "failed": failed},
            "info": info,
        }
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"报告: {a.json}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
