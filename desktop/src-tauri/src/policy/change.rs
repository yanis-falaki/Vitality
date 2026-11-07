//! Change detection (R4): has a screen changed since its last shot?
//!
//! `thumb(image)` shrinks a frame to a 64×36 grayscale `Thumb` (each cell the mean luma of its
//! area); `changed(a, b, t)` is true when more than `t.fraction` of the cells differ by more than
//! `t.levels`. Small things (a clock digit, the cursor, the pointer) touch a few cells and do not
//! count; a new line of text or a scroll touches many.

use image::RgbImage;
use serde::{Deserialize, Serialize};

pub const THUMB_W: usize = 64;
pub const THUMB_H: usize = 36;

/// A 64×36 grayscale summary of one frame.
#[derive(Clone, PartialEq, Eq)]
pub struct Thumb(pub Box<[u8; THUMB_W * THUMB_H]>);

impl std::fmt::Debug for Thumb {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mean = self.0.iter().map(|&v| v as u32).sum::<u32>() / self.0.len() as u32;
        write!(f, "Thumb(mean {mean})")
    }
}

impl Thumb {
    /// A thumb of one flat gray level (for tests and placeholders).
    pub fn flat(level: u8) -> Thumb {
        Thumb(Box::new([level; THUMB_W * THUMB_H]))
    }
}

/// How different two thumbs must be to count as a change.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Threshold {
    /// The share of cells that must differ (0.005 = 0.5 %).
    pub fraction: f64,
    /// By more than this many gray levels (0–255).
    pub levels: u8,
}

impl Default for Threshold {
    fn default() -> Self {
        Threshold { fraction: 0.005, levels: 16 }
    }
}

/// The 64×36 grayscale thumb of `image`: each cell is the mean luma of its area, sampled on a
/// grid of at most 16×16 points so a 4K frame costs about the same as a small one.
pub fn thumb(image: &RgbImage) -> Thumb {
    let (w, h) = (image.width() as usize, image.height() as usize);
    let mut out = Box::new([0u8; THUMB_W * THUMB_H]);
    if w == 0 || h == 0 {
        return Thumb(out);
    }
    let raw = image.as_raw();
    for cy in 0..THUMB_H {
        let (y0, y1) = (cy * h / THUMB_H, ((cy + 1) * h / THUMB_H).max(cy * h / THUMB_H + 1).min(h));
        let ystep = ((y1 - y0) / 16).max(1);
        for cx in 0..THUMB_W {
            let (x0, x1) = (cx * w / THUMB_W, ((cx + 1) * w / THUMB_W).max(cx * w / THUMB_W + 1).min(w));
            let xstep = ((x1 - x0) / 16).max(1);
            let (mut sum, mut n) = (0u32, 0u32);
            for y in (y0..y1).step_by(ystep) {
                let row = y * w * 3;
                for x in (x0..x1).step_by(xstep) {
                    let p = row + x * 3;
                    // Rec. 601 luma in integers: (77 R + 150 G + 29 B) / 256.
                    sum += (77 * raw[p] as u32 + 150 * raw[p + 1] as u32 + 29 * raw[p + 2] as u32) >> 8;
                    n += 1;
                }
            }
            out[cy * THUMB_W + cx] = (sum / n.max(1)) as u8;
        }
    }
    Thumb(out)
}

/// Whether `b` differs from `a` by the threshold's rule.
pub fn changed(a: &Thumb, b: &Thumb, t: &Threshold) -> bool {
    let differing = a.0.iter().zip(b.0.iter()).filter(|(x, y)| x.abs_diff(**y) > t.levels).count();
    differing as f64 > t.fraction * (THUMB_W * THUMB_H) as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Rgb;

    const W: u32 = 1920;
    const H: u32 = 1080;
    const PAPER: Rgb<u8> = Rgb([250, 250, 248]);
    const INK: Rgb<u8> = Rgb([30, 30, 30]);

    fn rect(img: &mut RgbImage, x: u32, y: u32, w: u32, h: u32, c: Rgb<u8>) {
        for yy in y..(y + h).min(img.height()) {
            for xx in x..(x + w).min(img.width()) {
                img.put_pixel(xx, yy, c);
            }
        }
    }

    /// A line of "text": glyph-sized blocks with gaps, about a third ink.
    fn text_line(img: &mut RgbImage, x: u32, y: u32, chars: u32) {
        for i in 0..chars {
            rect(img, x + i * 12, y + 4, 7, 14, INK);
        }
    }

    /// A page of text lines starting at `top`, like a chat or a document.
    fn page(top: u32) -> RgbImage {
        let mut img = RgbImage::from_pixel(W, H, PAPER);
        let mut y = top;
        while y + 24 < H - 40 {
            text_line(&mut img, 80, y, 100);
            y += 28;
        }
        // A status bar with a clock.
        rect(&mut img, 0, H - 32, W, 32, Rgb([220, 220, 216]));
        img
    }

    fn clock(img: &mut RgbImage, digit: u32) {
        rect(img, W - 60, H - 26, 40, 20, Rgb([220, 220, 216]));
        rect(img, W - 60 + digit * 3, H - 26, 8, 20, INK);
    }

    fn t() -> Threshold {
        Threshold::default()
    }

    #[test]
    fn identical_frames_are_unchanged() {
        assert!(!changed(&thumb(&page(40)), &thumb(&page(40)), &t()));
    }

    #[test]
    fn a_clock_digit_is_no_change() {
        let mut a = page(40);
        let mut b = a.clone();
        clock(&mut a, 1);
        clock(&mut b, 7);
        assert!(!changed(&thumb(&a), &thumb(&b), &t()));
    }

    #[test]
    fn the_cursor_and_the_pointer_are_no_change() {
        let a = page(40);
        let mut b = a.clone();
        rect(&mut b, 80 + 100 * 12, 44, 2, 18, INK); // text cursor at the end of a line
        rect(&mut b, 900, 500, 14, 22, INK); // the mouse pointer moved here
        rect(&mut b, 901, 501, 10, 16, Rgb([255, 255, 255]));
        assert!(!changed(&thumb(&a), &thumb(&b), &t()));
    }

    #[test]
    fn a_new_message_line_is_a_change() {
        let mut a = RgbImage::from_pixel(W, H, PAPER);
        text_line(&mut a, 80, 100, 100);
        let mut b = a.clone();
        text_line(&mut b, 80, 128, 60);
        assert!(changed(&thumb(&a), &thumb(&b), &t()));
    }

    #[test]
    fn a_scroll_is_a_change() {
        assert!(changed(&thumb(&page(40)), &thumb(&page(54)), &t()));
    }

    #[test]
    fn threshold_counts_cells_strictly_above_the_fraction() {
        let a = Thumb::flat(100);
        let mut b = Thumb::flat(100);
        // 0.5 % of 2304 cells is 11.52: 11 cells are not enough, 12 are.
        for v in b.0.iter_mut().take(11) {
            *v = 200;
        }
        assert!(!changed(&a, &b, &t()));
        b.0[11] = 200;
        assert!(changed(&a, &b, &t()));
        // A difference of exactly `levels` does not count.
        assert!(!changed(&Thumb::flat(100), &Thumb::flat(116), &t()));
        assert!(changed(&Thumb::flat(100), &Thumb::flat(117), &t()));
    }

    #[test]
    fn thumb_of_tiny_and_empty_images() {
        assert_eq!(thumb(&RgbImage::from_pixel(10, 5, Rgb([255, 255, 255]))), Thumb::flat(255));
        assert_eq!(thumb(&RgbImage::new(0, 0)), Thumb::flat(0));
    }
}
