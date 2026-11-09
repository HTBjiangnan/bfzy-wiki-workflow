# 游戏资源解包（取真值）

> wiki 上的星级、四维、技能、天赋、图标，最终都要以**游戏客户端里的配置表**为准。
> 本目录把"从模拟器里把真值挖出来"这条链路固定下来。

公告能告诉你"出了什么卡"，但告不准"这张卡 20 级的攻击力是多少"。
所以每次版本更新的第二步，是把客户端解包、拿配置表对账。

## 链路总览

```
模拟器（MuMu 实例 0，adb 端口 16384）
   │  pull_db.py            纯 socket adb，流式落盘 ~12 MB/s
   ▼
raw/_DBreezeResources.bin   热点库（单文件 DBreeze 数据库，约 1.9 GB）
   │  extract_cfg.py        只扫需要的表名（3 表约 3 秒）
   ▼
cfg/<table>.occ1..N         每张表的全部历史版本（LZ4 帧，已解压）
   │  promote_cfg.py        取 occ 编号最大的当权威；顺手解析天赋表
   ▼
cfg/all/<table>.occ2        mercenarycfgs（角色）/ armamentcfgs（装备）/ flaircfgs（天赋）
cfg/all/flairs.json         天赋表解析结果：Name / Icon / TxtIcon / Desc
   │
   ├─ scan_tex_keys.py      → tex_keys.json（贴图键名 → 偏移）
   │      └─ tex_decode.py  → 单张纹理解码（修正版）
   │             └─ extract_icons.py → tex_out/icons/<天赋名>.png
   ▼
角色等级四维 / 技能文本 / 图标，全部可用于回填 wiki
```

## 快速开始

```bash
# 0. 先确认模拟器开着、端口通（MuMu 实例 0 = 16384）
python extract/adb.py "ls /sdcard/Android/data/com.woowaygames.onepercent/files/Assets/Database"

# 1. 拉热点库（1.9 GB，约 150 秒）
python extract/pull_db.py --out raw/_DBreezeResources.bin

# 2. 定向提取三张表
python extract/extract_cfg.py --src raw/_DBreezeResources.bin --out cfg \
    --tables cfg.mercenarycfgs,cfg.armamentcfgs,cfg.flaircfgs

# 3. 提级为权威数据 + 解析天赋表（--anchor 是自检用的锚点天赋名）
python extract/promote_cfg.py --src cfg --dst cfg/all \
    --anchor 钢铁之躯,聚能刻印,人之力

# 4. 建贴图索引（全库扫一遍，约 1 分钟）
python extract/scan_tex_keys.py --src raw/_DBreezeResources.bin --out tex_keys.json

# 5. 导出全部天赋图标
python extract/extract_icons.py --src raw/_DBreezeResources.bin \
    --index tex_keys.json --flairs cfg/all/flairs.json --out tex_out/icons
```

## 关键结论（踩过的坑，别再踩）

| 项 | 结论 |
|---|---|
| **adb 端口** | MuMu 实例 0 是 **16384**，不是 16385/5555。`adb.exe` 守护进程在部分机器上起不来（安全策略），所以本目录用纯 socket 直连，不依赖 adb.exe |
| **拉包实现** | 必须**流式落盘**。先收完再解析的实现会退化成 O(n²)，1.9 GB 根本跑不动；本实现 8 MB 缓冲、约 12 MB/s |
| **DBreeze 记录结构** | `$k` 标记 = `19 01` + u32(2) + `$k` + u32(主键)；字段名 = u32(len) + utf16(name)；字段值紧跟 `01` 类型字节，再 u32(len) + utf16(value)。**值起点是 `p + len(字段名tag)`，写成 `p+4` 会 0 命中** |
| **取值窗口** | 相邻同名表的偏移当窗口上界，窗口内可能混入后续对象，`lz4.frame.decompress` 默认解第一帧即可 |
| **贴图键名** | `utex2.icon_<ID>_<拼音>`（157 个）+ `utex2.icon_<拼音>`（383 个，含功能图标）+ `utex2.tt_talent_<拼音>`（51 个，另一套，与前者零重叠，一般不用） |
| **图标映射** | **必须用 flaircfgs 的 `Icon` 字段**。靠名字猜拼音会张冠李戴：灵力膨胀(ID470)=`icon_llpz`，而 `icon_496_llpz` 是"灵力屏障" |
| **纹理解码 bug** | UnityPy 取数组长度的方式是"第一个 1000<int32<20M"，小图上会抓到 2048 → 解出花屏。正解：起点 = `System.Byte[], mscorlib` 之后 +8~24（实测 +15），长度 = 解压总长 − 起点。见 `tex_decode.py` |
| **纯白不是缺色** | 天赋图标是 **RGB 三通道相等的纯白 + alpha 通道**（游戏内靠 tint 上色），所以 `smooth≈0` 是正常结果，不是解空 |
| **装备图标未解** | `a_armament_*.png`（512×512）目前解出来是竖条纹错位，转置/镜像/换尺寸均未修复，可能另有 tiled 排列。角色页有模板守卫兜底，不影响正文 |
| **配置残留** | 个别天赋（如"究极火花"）`Icon` 指向的键在两个版本库里都不存在，游戏内也是空图 —— 属配置残留，不要硬补 |

## 依赖

```
lz4          # DBreeze 值压缩
UnityPy      # Texture2D 解码
Pillow       # 存 PNG
numpy        # 平滑度判据
```

`texture2ddecoder` 可选（想绕开 UnityPy 直接解 ETC2 时用）。

## 与 wiki 的接口

- 角色四维：`mercenarycfgs` 的 `Level` 段，键序 `k0=生命值 k1=护甲值 k2=攻击力 k3=灵力值`
- 能量值 / 属性：`armamentcfgs` 的 `EnergyValue` / `EnergyIcon`（`zhu_duhuo`=毒·火 之类）
- 技能文本：`SkillDescFmt`
- 图标：`flaircfgs.Icon` → `utex2.<Icon>` → 256×256 PNG
