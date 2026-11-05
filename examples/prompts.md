# 提示词速查（Prompt Cheatsheet）

装成 Skill 后，智能体读 `SKILL.md` 里的需求映射表来理解你的话。
本页汇总可直接复制的说法，按意图分组——**复制、改地名、回车**。

## 一、取数类

| 你说 | 智能体会跑 |
|---|---|
| 「下载 XX 县的 30 米 DEM」 | `cn_dem.py XX县 out` |
| 「XX 市全域的高程」 | `cn_dem.py XX市 out` |
| 「朝阳区……不对，是长春那个」 | `cn_dem.py 朝阳区 out --parent 长春市` |
| 「经纬度 117.2,31.8 到 117.45,32.05 这块」 | `cn_dem.py hefei out --bbox "117.2,31.8,117.45,32.05"` |
| 「按我给的 geojson 裁」 | `cn_dem.py area out --geojson my.json` |
| 「边界外多留 1 公里」 | `--buffer 1000` |
| 「预览图要白底，要贴 PPT」 | `cn_dem.py XX县 out --bg white` |
| 「C 盘满了，清下缓存」 | `cn_dem.py --clean-cache` |

## 二、分析类

| 你说 | 智能体会跑 |
|---|---|
| 「坡度图、坡向图都要」 | `terrain_products.py dem.tif --all` |
| 「阴影方向换成东南光」 | `terrain_products.py dem.tif --hillshade --az 135 --alt 45` |
| 「坡度夸大 2 倍看立体感」 | `--z-factor 2.0` |
| 「高程统计：分位数、面积、直方图」 | `dem_stats.py dem.tif --json stats.json --csv hist.csv` |
| 「直方图分带细一点，10 米一带」 | `dem_stats.py dem.tif --bin 10 --csv hist.csv` |
| 「转成椭球高，要和 RTK 点比对」 | `datum_convert.py in.tif out.tif --to ellipsoidal` |

## 三、质检与交付类

| 你说 | 智能体会跑 |
|---|---|
| 「验收一下这份 DEM」 | `verify_output.py dem.tif -b 边界.json` |
| 「验收报告存档进交付包」 | `verify_output.py dem.tif -b 边界.json --json report.json` |
| 「做一个能转的三维地形给甲方看」 | `export_web3d.py dem.tif --name XX` |
| 「三维精细一点，机器性能好」 | `export_web3d.py dem.tif --target 900` |

## 四、复合任务（智能体自动拆步骤）

```text
把安康市下面 10 个县区的 DEM 全部准备好，统计表汇总成一张 Excel。
```
→ 智能体逐县调用 `cn_dem.py`（同市瓦片共享缓存，只下一次），
再逐县 `dem_stats.py`，最后把 JSON 汇总成表并口述异常项。

```text
这个风电场周边 20 km 范围，坡度小于 15° 的连片平缓区帮我圈出来，
输出一张叠加了候选区块的预览图。
```
→ `cn_dem.py --bbox` 取范围 → `terrain_products.py --slope` → 智能体用 numpy
做连通域筛选 → 用预览底图合成标注图。

```text
项目明天评审，给我三样东西：全县三维地形（可旋转）、
高程面积统计表、一张能贴 PPT 的山体阴影图。
```
→ `export_web3d.py` + `dem_stats.py` + `preview_dem.py --bg white`，一条消息三件套。

## 五、写提示词的三个习惯

1. **说地名要说全**：全国有多个「朝阳区」「城关区」；跨省同名时补一句上级市，或直接给 adcode。
2. **说清用途**：「做底图」和「算土方」走的是不同口径——后者会触发基准换算与实测校准提醒。
3. **要验收就说一声**：「跑完验收一下」会触发 `verify_output.py`，交付前多一道保险；
   不说则默认只出图（快）。
