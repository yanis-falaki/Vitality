"""Combining several monitors' frames into one whole-desktop image."""

from PIL import Image


# == Contract =============================================================================

def composite(frames: list[tuple[Image.Image, tuple[int, int]]]) -> Image.Image:
    """Place each frame at its (x, y) on a black canvas sized to the bounding box.

    Positions may be negative; everything is shifted so the minimum becomes 0.
    A single frame is returned unchanged (same object).
    """
    if len(frames) == 1:
        return frames[0][0]
    min_x = min(x for _, (x, _) in frames)
    min_y = min(y for _, (_, y) in frames)
    max_x = max(x + img.width for img, (x, _) in frames)
    max_y = max(y + img.height for img, (_, y) in frames)
    canvas = Image.new("RGB", (max_x - min_x, max_y - min_y), (0, 0, 0))
    for img, (x, y) in frames:
        canvas.paste(img, (x - min_x, y - min_y))
    return canvas


def layout_frames(
    frames: list[tuple[Image.Image, tuple[int, int], tuple[int, int]]],
) -> list[tuple[Image.Image, tuple[int, int]]]:
    """Map (frame, logical position, logical size) to physical-pixel placements for composite.

    Positions and sizes are logical (compositor) units; frames are physical pixels. The
    scale factor is the largest frame-width / logical-width ratio, so the sharpest monitor
    keeps its native pixels and lower-scale monitors are upscaled to match. Frames already
    at their target size are returned as the same object (no resize).
    """
    factor = max(img.width / size[0] for img, _, size in frames)
    laid = []
    for img, (x, y), (w, h) in frames:
        target = (round(w * factor), round(h * factor))
        if img.size != target:
            img = img.resize(target, Image.Resampling.BICUBIC)
        laid.append((img, (round(x * factor), round(y * factor))))
    return laid
