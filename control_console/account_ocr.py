"""Local-only Windows OCR and conservative coordinate-based table reconstruction."""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 10000


def recognize_image(data: bytes) -> dict:
    """Recognize a validated upload in a short-lived system Windows PowerShell process."""
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("本地截图识别需要带Pillow的本机Python运行环境") from exc
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("截图为空或超过20MB限制")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise ValueError("仅支持PNG/JPEG截图")
            width, height = image.size
            if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
                raise ValueError("截图超过10000边长或4000万像素限制")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("截图格式无效或文件已截断") from exc
    if os.name != "nt":
        raise RuntimeError("本地截图识别需要Windows OCR")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = Path(__file__).resolve().parents[1] / "tools/account_ocr.ps1"
    with tempfile.TemporaryDirectory(prefix="console-account-ocr-") as directory:
        source = Path(directory) / "upload.img"
        source.write_bytes(data)
        try:
            completed = subprocess.run(
                [str(executable), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-ImagePath", str(source)],
                capture_output=True, timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("本地OCR超时，请缩小截图后重试") from exc
        except OSError as exc:
            raise RuntimeError("无法启动系统Windows PowerShell本地OCR") from exc
        if completed.returncode:
            raise RuntimeError("本地OCR不可用，请确认已安装简体中文OCR语言组件")
        try:
            result = json.loads(completed.stdout.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("本地OCR返回格式异常") from exc
        if result.get("width") != width or result.get("height") != height or not isinstance(result.get("lines"), list):
            raise RuntimeError("本地OCR返回尺寸异常")
        with Image.open(io.BytesIO(data)) as image:
            result.update(detect_table_grid(image, result))
        return result


def detect_table_grid(image, result):
    """Require continuous neutral grid lines AND eight OCR header anchors.

    Narrow line runs only: filled bands, text and letter midpoints are never
    treated as separators. Coordinates remain in the original uploaded image.
    """
    words = [word for line in result.get("lines", []) for word in line.get("words", [])]
    letters = [word for word in words if str(word.get("text", "")).strip().upper() in list("ABCDEFGH")]
    rgb = image.convert("RGB")
    pixels = rgb.load()
    width, height = image.size

    def ink(px, py):
        colors = pixels[px, py]
        return max(colors) <= 245 and max(colors)-min(colors) <= 20

    def thin_centers(matches):
        runs = []
        for value in matches:
            if runs and value == runs[-1][-1]+1:
                runs[-1].append(value)
            else:
                runs.append([value])
        return [sum(run)/len(run) for run in runs if len(run) <= 4]

    for anchor in letters:
        band = sorted([word for word in letters if abs(word["y"]-anchor["y"]) <= min(word["height"],anchor["height"])*.5], key=lambda word: word["x"])
        if [str(word["text"]).strip().upper() for word in band] != list("ABCDEFGH"):
            continue
        centers = [word["x"]+word["width"]/2 for word in band]
        start = int(max(word["y"]+word["height"] for word in band))+1
        left, right = int(centers[0]), int(centers[-1])
        if right-left < 80 or start >= height:
            continue
        samples_x = list(range(left, right+1, max(1,(right-left)//250)))
        horizontal = thin_centers([py for py in range(start,height) if sum(ink(px,py) for px in samples_x)/len(samples_x) >= .97])
        if len(horizontal) < 3:
            continue
        top, bottom = int(horizontal[0]), int(horizontal[-1])
        if bottom-top < 20:
            continue
        samples_y = list(range(top,bottom+1,max(1,(bottom-top)//250)))
        vertical = thin_centers([px for px in range(width) if sum(ink(px,py) for py in samples_y)/len(samples_y) >= .97])
        intervals = []
        for center in centers:
            enclosing = [(a,b) for a,b in zip(vertical,vertical[1:]) if a < center < b]
            if len(enclosing) != 1:
                break
            intervals.append(enclosing[0])
        if len(intervals) != 8 or any(a[1] != b[0] for a,b in zip(intervals,intervals[1:])):
            continue
        columns = [intervals[0][0]]+[interval[1] for interval in intervals]
        # Ensure each whole header word lies within its verified cell.
        if any(word["x"] <= columns[i] or word["x"]+word["width"] >= columns[i+1] for i,word in enumerate(band)):
            continue
        return dict(gridColumns=columns, rowBoundaries=horizontal, gridHeaderBottom=start)
    return {}


def _boundaries(value, count, low, high, label):
    if not isinstance(value, list) or (count and len(value) != count) or len(value) < 2:
        raise ValueError(label + "边界数量无效")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        raise ValueError(label + "边界必须为有限数字")
    if value[0] < low or value[-1] > high or any(a >= b for a, b in zip(value, value[1:])):
        raise ValueError(label + "边界必须在截图范围内严格递增")
    return list(value)


def reconstruct_rows(result: dict, options=None) -> dict:
    """Return eight review-only fields. Ambiguous columns always require calibration.

    All coordinates, including crop/column/row boundaries, use original image pixels.
    Wrapped text needs explicit rowBoundaries to merge into the same table row.
    """
    options = options or {}
    width, height = result["width"], result["height"]
    crop = options.get("crop") or dict(x=0, y=0, width=width, height=height)
    values = [crop.get(k) for k in ("x", "y", "width", "height")]
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        raise ValueError("裁剪范围无效")
    x, y, w, h = values
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > width or y+h > height:
        raise ValueError("裁剪范围超出截图")
    words = []
    warnings = ["OCR结果需逐项人工核对；不提供识别置信度。"]
    for line in result.get("lines", []):
        for word in line.get("words", []):
            box = [word.get(k) for k in ("x", "y", "width", "height")]
            if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
                continue
            wx, wy, ww, wh = box
            if ww <= 0 or wh <= 0:
                continue
            if wx+ww > x and wx < x+w and wy+wh > y and wy < y+h:
                words.append(dict(word))
    columns = options.get("columns")
    if columns is None:
        proposed = result.get("gridColumns")
        if proposed and proposed[0] >= x and proposed[-1] <= x+w:
            columns = proposed
            words = [word for word in words if word["y"] >= result.get("gridHeaderBottom",0)]
            warnings.append("列边界由连续表格线与A:H表头共同验证，仍需人工确认。")
    if columns is None:
        return dict(rows=[], warnings=warnings+["无法可靠识别八列边界，请手动校准A:H九条列边界。"], columns=[], needsCalibration=True)
    columns = _boundaries(columns, 9, x, x+w, "列")
    boundaries = options.get("rowBoundaries")
    if boundaries is None and columns == result.get("gridColumns"):
        detected = [value for value in result.get("rowBoundaries",[]) if y <= value <= y+h]
        if len(detected) >= 2:
            boundaries = detected
            warnings.append("行边界来自连续水平表格线，请检查首尾是否完整。")
    groups = []
    if boundaries is not None:
        boundaries = _boundaries(boundaries, None, y, y+h, "行")
        groups = [[] for _ in range(len(boundaries)-1)]
        for word in words:
            cy = word["y"]+word["height"]/2
            for index, (top, bottom) in enumerate(zip(boundaries,boundaries[1:])):
                if top <= cy < bottom:
                    groups[index].append(word)
                    break
    else:
        warnings.append("未校准行边界：换行单元格可能分成多行，请检查或手动校准。")
        for word in sorted(words, key=lambda a:(a["y"]+a["height"]/2,a["x"])):
            cy = word["y"]+word["height"]/2
            if groups and abs(cy-(groups[-1][0]["y"]+groups[-1][0]["height"]/2)) <= min(word["height"], groups[-1][0]["height"])*.5:
                groups[-1].append(word)
            else:
                groups.append([word])
    rows = []
    for group in groups:
        cells, issues = [[] for _ in range(8)], []
        for word in group:
            cx = word["x"]+word["width"]/2
            for index, (left,right) in enumerate(zip(columns,columns[1:])):
                if left <= cx < right:
                    cells[index].append(word)
                    if word["x"] < left or word["x"]+word["width"] > right:
                        issues.append("文字跨越列边界，请校准或人工修正")
                    if word["x"] <= x or word["y"] <= y or word["x"]+word["width"] >= x+w or word["y"]+word["height"] >= y+h:
                        issues.append("文字接触裁剪边缘，可能被截断")
                    break
            else:
                issues.append("存在列范围外文字，请检查裁剪或列边界")
        fields = [" ".join(str(a["text"]) for a in sorted(cell,key=lambda a:(a["y"],a["x"]))) for cell in cells]
        if any(fields) or issues:
            rows.append(dict(fields=fields, boxes=cells, issues=list(dict.fromkeys(issues))))
    return dict(rows=rows, warnings=warnings, columns=columns, needsCalibration=False)
