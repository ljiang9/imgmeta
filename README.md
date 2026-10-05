# imgmeta

从文件头读取图片元数据，零依赖（只用 Python 标准库）。

不装 Pillow、不调外部命令，读几个字节的文件头就知道一张图的格式和尺寸。

## 安装

```bash
cd imgmeta
python3 -m imgmeta photo.png
```

Python 3.10+，无第三方依赖。

## 用法

```bash
# 单个文件：信息卡
python3 -m imgmeta photo.png

# 扫描目录：表格
python3 -m imgmeta --dir ./photos

# JSON 输出（脚本用）
python3 -m imgmeta photo.png --json
python3 -m imgmeta --dir ./photos --json
```

单文件输出示例：

```
文件：photo.png（24.6 KB）
格式：PNG
尺寸：320 × 200
位深：8 位
颜色：真彩色（RGB）
文本块 [Title]：Hand-crafted test
```

目录扫描输出示例：

```
  文件                           格式         尺寸             大小
  ------------------------------------------------------------------
  a.png                        PNG        320x200         80 B
  b.jpg                        JPEG       640x480         35 B
  e.webp                       WebP       640x480         30 B

共 3 个成功，0 个失败。
```

## 支持的格式

| 格式 | 读取内容 |
|------|---------|
| PNG | IHDR（尺寸/位深/颜色类型）+ tEXt 文本块 |
| JPEG | SOF0–SOF3 等标记（尺寸/精度），跳过 APPn/填充字节 |
| GIF | 逻辑屏幕描述符（87a / 89a） |
| BMP | BITMAPINFOHEADER / BITMAPCOREHEADER（尺寸/位深） |
| WebP | VP8（有损）、VP8L（无损）、VP8X（扩展画布） |

损坏或未知格式给出中文错误并返回 exit 1，不抛 traceback。

## 设计取舍

- **只读文件头**：不解码像素，不解析 EXIF。想要拍摄参数请用 exiftool。
- **PNG CRC 不校验**：元数据工具只关心尺寸，跳过 CRC（损坏的 CRC 不影响读尺寸）。
- **BMP 高度取绝对值**：自上而下存储的 BMP 高度为负数，显示时取绝对值。
- **单文件 `--json` 输出对象**，`--dir --json` 输出数组。

## 已知局限

- 不解析 EXIF / XMP / ICC 等元数据块（WebP VP8X 只读画布尺寸，不进内嵌数据块）。
- JPEG 只认标准 SOF 标记；极少数非标准编码器写出的文件可能读不到。
- BMP 只支持最常见的两种 DIB 头（40 字节 BITMAPINFOHEADER、12 字节 BITMAPCOREHEADER）。
- 尺寸来自文件头自述：如果文件头被恶意伪造，读到的就是伪造值（和 `file` 命令一样）。
