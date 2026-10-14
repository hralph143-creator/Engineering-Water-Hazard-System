# 数据目录说明

本项目训练与质量校验使用官方 **Sen1Floods11 v1.1** 数据集
（cloudtostreet/Sen1Floods11，GCS bucket `gs://sen1floods11`；
论文：Bonafilia et al., 2020）。

> `v1.1` 是 Sen1Floods11 数据集格式版本，不是本系统的软件版本号或软著版本号。

## 训练与质量校验数据放在哪里

**本机真实数据放 E 盘（不写 C 盘）：**

```
E:\floodrisk\datasets\Sen1Floods11\
└── v1.1/
    ├── data/
    │   └── flood_events/
    │       └── HandLabeled/
    │           ├── S1Hand/      # {EVENT_CHIPID}_S1Hand.tif（2 band Float32, band0=VV band1=VH）
    │           └── LabelHand/   # {EVENT_CHIPID}_LabelHand.tif（1 band Int16, -1/0/1）
    └── splits/
        └── flood_handlabeled/
            ├── flood_train_data.csv   # 252 行，每行 "chip_S1Hand.tif,chip_LabelHand.tif"
            ├── flood_valid_data.csv   # 89 行
            └── flood_test_data.csv    # 90 行
```

- 下载命令：`python scripts/download_sen1floods11.py --out E:\floodrisk\datasets\Sen1Floods11`
  （GCS 匿名 HTTP，无需 gsutil；只下载本阶段需要的 S1Hand + LabelHand + split）。
- 项目内 `data/raw/` 不再存放真实数据（大文件不入仓库），仅保留占位。

## 应用运行数据放在哪里

训练数据与系统运行时导入的数据相互独立。用户在“遥感数据管理”中导入的双波段 GeoTIFF 会复制到 `configs/app.yaml` 的 `paths.uploads`，识别结果写入 `paths.outputs`，任务与元数据写入 `paths.database`。默认示例为：

```text
E:\floodrisk\app_data\uploads\       应用导入影像
E:\floodrisk\app_data\floodrisk.db  SQLite 数据库
E:\floodrisk\app_outputs\            概率图、掩膜、预览图和导出文件
```

应用推理只要求输入影像包含 VV、VH 两个波段，不要求同时提供训练阶段使用的 QC 标签。

## 官方 v1.1 格式要点

| 图层 | 内容 | 值 | 格式 |
| --- | --- | --- | --- |
| S1 | Sentinel-1 GRD（IW），单位 dB | — | 512×512，2 band Float32，band0=VV band1=VH，EPSG:4326，10m |
| QC | 手绘标签 | -1=NoData/Not Valid，0=Not Water，1=Water | 512×512，1 band Int16 |

- 文件命名：`EVENT_CHIPID_LAYER.tif`（如 `Bolivia_103757_S1Hand.tif`）。
- S1 与 QC 完全配准（shape / CRS / transform 一致），程序会逐样本校验。
- S1 的 nodata 以 NaN 标记；QC 的无效像素为 -1，损失与指标会忽略。

## 训练派生数据（E:\floodrisk\processed\，自动生成）

```
processed/
├── splits/                    # train/val/test.txt（chip id 清单，来自官方 CSV）
└── sar_stats.json             # VV/VH 裁剪与归一化参数（仅训练集统计，全流程共用）
```

## 训练与评估输出（E:\floodrisk\outputs\，自动生成）

- `dataset_index.csv` + `train/val/test_samples.csv`：样本索引与划分明细
- `metrics/`：数据摘要、训练历史、测试指标、实验记录
- `predictions/`：概率图 / 掩膜 / 叠加图 / 对比图
- `figures/`、`real_samples/`：样本图与训练曲线

## 重要约定

- `data/demo/` 中的是**随机张量 demo 数据**，仅用于测试代码结构，**严禁**作为正式训练数据；
- 不要将任何大文件提交到 Git；真实数据、模型、输出均放 E 盘。
- 不要在识别任务运行期间移动或删除 `paths.uploads`、`paths.outputs` 或数据库文件。

