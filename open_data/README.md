# 开源数据目录

本目录保存已核验来源、可追溯且与工程水害业务相关的公开数据。下载日期：2026-10-14。

## 已下载内容

| 分区 | 数据 | 格式 | 用途 | 状态 |
|---|---|---|---|---|
| 水系 | Natural Earth 1:10m 河流与湖泊中心线 | Shapefile / ZIP | 水系背景、空间参照 | 已下载并解压 |
| 水系 | Natural Earth 1:10m 湖泊与水库 | Shapefile / ZIP | 水体背景、空间参照 | 已下载并解压 |
| 地形 | Copernicus DEM GLO-30 N39 E116 | Cloud Optimized GeoTIFF | 北京示例区地形数据 | 已下载并导入系统 |
| 地形 | Copernicus DEM GLO-30 瓦片清单与 STAC 元数据 | TXT / JSON / XML | 按项目范围定位后续 DEM 瓦片 | 已下载 |
| 卫星 | Copernicus Data Space Sentinel-1 产品目录样本 | JSON | SAR 产品字段与下载定位 | 已下载 100 条目录记录 |
| 降雨 | NASA POWER 北京点位 2025 日降雨 | CSV | 降雨字段与导入格式参考 | 已下载 365 日 |
| 模拟 | 全部 26 张业务表，每表 5 行 | XLSX | 功能联调与导出格式样例 | 已生成 |

## 目录结构

- `hydrology/`：全球河流、湖泊和水库矢量，已保留压缩包及解压文件。
- `terrain/`：DEM 实际瓦片、瓦片目录、产品说明和 STAC 元数据。
- `satellite/`：Sentinel-1 官方产品目录字段样本；不包含体量很大的原始 SAFE 产品。
- `rainfall/`：真实公开降雨样例。坐标为北京 `116.4074, 39.9042`，不能当作其他项目现场观测。
- `simulated_samples/`：带显著“模拟”标记的全业务表工作簿，不写入正式数据库。
- `licenses/`：随下载保存的许可或使用条款页面。

## 使用边界

1. Natural Earth 数据适合区域和全球背景表达，不适合施工场地级精细测量。
2. 已下载 DEM 为北京示例瓦片，范围约 `116°E–117°E, 39°N–40°N`。系统已建立 DEM 记录；其 CRS 为 EPSG:4326，当前系统不会直接据此计算米制坡度，坡度分析需先转换为当地合适的米制投影。
3. NASA POWER 文件是真实再分析数据，但不是现场雨量站实测；仅作为字段、流程和算法联调资料。
4. Sentinel-1 原始影像下载需要按项目范围和日期筛选，且 Copernicus Data Space 产品下载通常需要账户令牌。当前保存目录样本，避免无范围下载数 GB 数据。
5. 任何公开数据正式用于工程判断前，都必须核对空间范围、时间、坐标系、分辨率、许可和现场适用性。

## 官方来源

- Natural Earth：https://www.naturalearthdata.com/
- Copernicus DEM（AWS 开放数据）：https://registry.opendata.aws/copernicus-dem/
- Copernicus Data Space OData：https://documentation.dataspace.copernicus.eu/APIs/OData.html
- NASA POWER Daily API：https://power.larc.nasa.gov/docs/services/api/temporal/daily/
