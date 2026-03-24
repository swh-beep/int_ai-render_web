import os
from typing import Optional

from PIL import Image, ImageOps


def standardize_image(image_path, output_path=None, keep_ratio=False, force_landscape=False):
    try:
        if output_path is None:
            output_path = image_path
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)

            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGBA", img.size, (210, 210, 210, 255))
                img = Image.alpha_composite(background, img).convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size

            if force_landscape:
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            elif width >= height:
                target_size = (1920, 1080)
                target_ratio = 16 / 9
            else:
                target_size = (1080, 1350)
                target_ratio = 4 / 5

            if not keep_ratio:
                current_ratio = width / height
                if current_ratio > target_ratio:
                    new_width = int(height * target_ratio)
                    offset = (width - new_width) // 2
                    img = img.crop((offset, 0, offset + new_width, height))
                else:
                    new_height = int(width / target_ratio)
                    offset = (height - new_height) // 2
                    img = img.crop((0, offset, width, offset + new_height))

                img = img.resize(target_size, Image.Resampling.LANCZOS)

            base, _ = os.path.splitext(output_path)
            new_output_path = f"{base}.png"
            img.save(new_output_path, "PNG")
            return new_output_path
    except Exception as exc:
        print(f"!! 표준화 실패: {exc}", flush=True)
        return image_path


def set_png_dpi(path: str, dpi: tuple = (300, 300)) -> None:
    try:
        if not path or os.path.splitext(path)[1].lower() != ".png":
            return
        with Image.open(path) as img:
            img.save(path, "PNG", dpi=dpi)
    except Exception:
        pass


def _is_bottom_strip_mostly_white(
    img: Image.Image,
    strip_ratio: float = 0.22,
    white_thresh: int = 245,
) -> bool:
    try:
        width, height = img.size
        if width <= 0 or height <= 0:
            return False

        strip_h = max(1, int(height * strip_ratio))
        y0 = max(0, height - strip_h)
        strip = img.crop((0, y0, width, height))
        strip = strip.resize((256, max(1, int(256 * strip_ratio))), Image.Resampling.BILINEAR)
        gray = strip.convert("L")
        pixels = list(gray.getdata())
        if not pixels:
            return False

        white_count = sum(1 for pixel in pixels if pixel >= white_thresh)
        white_ratio = white_count / len(pixels)
        return white_ratio >= 0.35
    except Exception:
        return False


def standardize_image_to_reference_canvas(
    image_path: str,
    reference_path: str,
    output_path: Optional[str] = None,
) -> str:
    try:
        with Image.open(reference_path) as ref_img:
            ref_img = ImageOps.exif_transpose(ref_img)
            ref_w, ref_h = ref_img.size
            if ref_w <= 0 or ref_h <= 0:
                return image_path

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size
            if width <= 0 or height <= 0:
                return image_path

            target_ratio = ref_w / ref_h
            current_ratio = width / height

            if abs(current_ratio - target_ratio) < 1e-3 and (width, height) == (ref_w, ref_h):
                base, _ = os.path.splitext(output_path or image_path)
                out_path = f"{base}.png"
                img.save(out_path, "PNG")
                return out_path

            if current_ratio > target_ratio:
                new_w = int(height * target_ratio)
                x0 = max(0, (width - new_w) // 2)
                img = img.crop((x0, 0, x0 + new_w, height))
            else:
                new_h = int(width / target_ratio)
                new_h = min(new_h, height)
                if ref_w >= ref_h and _is_bottom_strip_mostly_white(img):
                    y0 = 0
                else:
                    y0 = max(0, (height - new_h) // 2)
                img = img.crop((0, y0, width, y0 + new_h))

            img = img.resize((ref_w, ref_h), Image.Resampling.LANCZOS)
            base, _ = os.path.splitext(output_path or image_path)
            out_path = f"{base}_fit.png"
            img.save(out_path, "PNG")
            return out_path
    except Exception as exc:
        print(f"!! [Canvas Fit Failed] {exc}", flush=True)
        return image_path


def standardize_image_to_target_canvas(
    image_path: str,
    target_path: str,
    output_path: Optional[str] = None,
) -> str:
    try:
        with Image.open(target_path) as target_img:
            target_img = ImageOps.exif_transpose(target_img)
            target_w, target_h = target_img.size
            if target_w <= 0 or target_h <= 0:
                return image_path

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size
            if width <= 0 or height <= 0:
                return image_path

            target_ratio = target_w / target_h
            current_ratio = width / height

            if abs(current_ratio - target_ratio) < 1e-3:
                resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            else:
                if current_ratio > target_ratio:
                    new_w = int(height * target_ratio)
                    x0 = max(0, (width - new_w) // 2)
                    img = img.crop((x0, 0, x0 + new_w, height))
                else:
                    new_h = int(width / target_ratio)
                    y0 = max(0, (height - new_h) // 2)
                    img = img.crop((0, y0, width, y0 + new_h))
                resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

            base, _ = os.path.splitext(output_path or image_path)
            out_path = f"{base}_target.png"
            resized.save(out_path, "PNG")
            return out_path
    except Exception as exc:
        print(f"!! [Target Canvas Fit Failed] {exc}", flush=True)
        return image_path


def match_aspect_to_target(
    image_path: str,
    target_path: str,
    output_path: Optional[str] = None,
) -> str:
    try:
        with Image.open(target_path) as target_img:
            target_img = ImageOps.exif_transpose(target_img)
            target_w, target_h = target_img.size
            if target_w <= 0 or target_h <= 0:
                return image_path

        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size
            if width <= 0 or height <= 0:
                return image_path

            target_ratio = target_w / target_h
            current_ratio = width / height
            if abs(current_ratio - target_ratio) < 1e-3:
                return image_path

            if current_ratio > target_ratio:
                new_w = int(height * target_ratio)
                x0 = max(0, (width - new_w) // 2)
                img = img.crop((x0, 0, x0 + new_w, height))
            else:
                new_h = int(width / target_ratio)
                y0 = max(0, (height - new_h) // 2)
                img = img.crop((0, y0, width, y0 + new_h))

            base, _ = os.path.splitext(output_path or image_path)
            out_path = f"{base}_aspect.png"
            img.save(out_path, "PNG")
            return out_path
    except Exception as exc:
        print(f"!! [Aspect Fit Failed] {exc}", flush=True)
        return image_path


def pad_image_to_target_canvas(
    img: Image.Image,
    target_w: int,
    target_h: int,
    pad_color: tuple = (255, 255, 255),
) -> Image.Image:
    try:
        if target_w <= 0 or target_h <= 0:
            return img
        width, height = img.size
        if width <= 0 or height <= 0:
            return img

        scale = min(1.0, target_w / width, target_h / height)
        if scale < 1.0:
            new_w = max(1, int(round(width * scale)))
            new_h = max(1, int(round(height * scale)))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            width, height = img.size

        canvas = Image.new("RGB", (target_w, target_h), pad_color)
        x0 = max(0, (target_w - width) // 2)
        y0 = max(0, (target_h - height) // 2)
        canvas.paste(img, (x0, y0))
        return canvas
    except Exception:
        return img
