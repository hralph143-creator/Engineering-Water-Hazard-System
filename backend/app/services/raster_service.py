import json
from pathlib import Path

import numpy as np
import rasterio


class RasterValidationError(ValueError):
    pass


def _stats(arr: np.ndarray) -> dict:
    finite = np.isfinite(arr)
    if not finite.any():
        raise RasterValidationError("波段不包含任何有限数值")
    values = arr[finite]
    return {
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": float(values.mean()),
        "nan_count": int(np.isnan(arr).sum()),
        "inf_count": int(np.isinf(arr).sum()),
    }


def inspect_geotiff(path: Path) -> dict:
    if path.suffix.lower() not in {".tif", ".tiff"}:
        raise RasterValidationError("仅支持 .tif 或 .tiff 格式的 GeoTIFF")
    try:
        with rasterio.open(path) as src:
            if src.count < 2:
                raise RasterValidationError(f"模型要求 VV/VH 双波段，当前文件仅有 {src.count} 个波段")
            if src.width <= 0 or src.height <= 0:
                raise RasterValidationError("影像宽度或高度无效")
            vv = src.read(1).astype(np.float32)
            vh = src.read(2).astype(np.float32)
            vv_stats, vh_stats = _stats(vv), _stats(vh)
            crs = src.crs.to_string() if src.crs else None
            return {
                "width": src.width,
                "height": src.height,
                "bands": src.count,
                "dtype": ", ".join(src.dtypes),
                "crs": crs,
                "transform": json.dumps(list(src.transform)[:6]),
                "bounds": json.dumps(list(src.bounds)),
                "nodata": None if src.nodata is None else str(src.nodata),
                "vv_stats": json.dumps(vv_stats, ensure_ascii=False),
                "vh_stats": json.dumps(vh_stats, ensure_ascii=False),
            }
    except RasterValidationError:
        raise
    except rasterio.errors.RasterioIOError as exc:
        raise RasterValidationError(f"文件不是可读取的 GeoTIFF：{exc}") from exc

