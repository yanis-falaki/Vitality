//! Frames from one screen-share: a GStreamer pipeline per screen that copies each frame out of
//! PipeWire's buffer as it arrives (`always-copy=true`, the buffer released at once), at most 2
//! a second, and keeps only the newest as `Latest` (an RGB image, its thumb, its arrival time).
//!
//! `FeedFactory::start(node_id, fd, slot)` starts a feed writing into `slot`; dropping the
//! returned `Feed` stops it. `GstFeeds` is the real factory; `to_rgb` (pure) turns the packed
//! pixel formats PipeWire offers into RGB.

use std::os::fd::{AsRawFd, OwnedFd};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use image::RgbImage;

use crate::policy::change::{thumb, Thumb};

/// A screen's newest frame.
#[derive(Clone)]
pub struct Latest {
    pub image: Arc<RgbImage>,
    pub thumb: Thumb,
    pub at: Instant,
}

impl Latest {
    pub fn new(image: RgbImage, at: Instant) -> Latest {
        Latest { thumb: thumb(&image), image: Arc::new(image), at }
    }
}

/// Where a feed puts its newest frame.
pub type Slot = Arc<Mutex<Option<Latest>>>;

/// A running feed; dropping it stops it.
pub trait Feed: Send + Sync {}

pub trait FeedFactory: Send + Sync {
    fn start(&self, node_id: u32, fd: OwnedFd, slot: Slot) -> Result<Box<dyn Feed>, String>;
}

/// The packed formats asked for, by the order of their bytes in memory.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PixelFormat {
    Bgrx,
    Rgbx,
    Xrgb,
    Xbgr,
    Bgr,
    Rgb,
}

impl PixelFormat {
    /// Bytes per pixel and the offsets of R, G and B in it.
    fn layout(self) -> (usize, [usize; 3]) {
        match self {
            PixelFormat::Bgrx => (4, [2, 1, 0]),
            PixelFormat::Rgbx => (4, [0, 1, 2]),
            PixelFormat::Xrgb => (4, [1, 2, 3]),
            PixelFormat::Xbgr => (4, [3, 2, 1]),
            PixelFormat::Bgr => (3, [2, 1, 0]),
            PixelFormat::Rgb => (3, [0, 1, 2]),
        }
    }
}

/// Copies a packed frame (`stride` bytes per row) into an RGB image; `None` if `data` is short.
pub fn to_rgb(format: PixelFormat, width: u32, height: u32, stride: usize, data: &[u8]) -> Option<RgbImage> {
    let (bpp, [r, g, b]) = format.layout();
    let (w, h) = (width as usize, height as usize);
    if h > 0 && (stride < w * bpp || data.len() < stride * (h - 1) + w * bpp) {
        return None;
    }
    let mut out = Vec::with_capacity(w * h * 3);
    for y in 0..h {
        let row = &data[y * stride..y * stride + w * bpp];
        for px in row.chunks_exact(bpp) {
            out.extend_from_slice(&[px[r], px[g], px[b]]);
        }
    }
    RgbImage::from_raw(width, height, out)
}

/// The real feeds: `pipewiresrc` → caps → `appsink`.
pub struct GstFeeds;

impl GstFeeds {
    pub fn new() -> Result<GstFeeds, String> {
        gstreamer::init().map_err(|e| format!("GStreamer cannot start: {e}"))?;
        Ok(GstFeeds)
    }
}

/// The pipeline for one share (the spec's: copied buffers, at most 2 frames a second, only
/// system-memory formats, the newest sample only).
pub fn pipeline_description(fd: i32, node_id: u32) -> String {
    format!(
        "pipewiresrc fd={fd} path={node_id} always-copy=true keepalive-time=1000 \
         ! video/x-raw,max-framerate=2/1,format={{BGRx,RGBx,xRGB,xBGR,BGRA,RGBA,ARGB,ABGR,BGR,RGB}} \
         ! appsink name=sink max-buffers=1 drop=true sync=false"
    )
}

struct GstFeed {
    pipeline: gstreamer::Pipeline,
    stop: Arc<AtomicBool>,
    _fd: OwnedFd, // the PipeWire remote; pipewiresrc uses it until the pipeline stops
}

impl Feed for GstFeed {}

impl Drop for GstFeed {
    fn drop(&mut self) {
        use gstreamer::prelude::*;
        self.stop.store(true, Ordering::Relaxed);
        let _ = self.pipeline.set_state(gstreamer::State::Null);
    }
}

impl FeedFactory for GstFeeds {
    fn start(&self, node_id: u32, fd: OwnedFd, slot: Slot) -> Result<Box<dyn Feed>, String> {
        use gstreamer::prelude::*;
        use gstreamer_app::{AppSink, AppSinkCallbacks};

        let pipeline = gstreamer::parse::launch(&pipeline_description(fd.as_raw_fd(), node_id))
            .map_err(|e| format!("cannot build the frame pipeline: {e}"))?
            .downcast::<gstreamer::Pipeline>()
            .map_err(|_| "the frame pipeline is not a pipeline".to_string())?;
        let sink = pipeline
            .by_name("sink")
            .and_then(|e| e.downcast::<AppSink>().ok())
            .ok_or("the frame pipeline has no appsink")?;
        let kept = Mutex::new(None::<Instant>);
        sink.set_callbacks(
            AppSinkCallbacks::builder()
                .new_sample(move |sink| {
                    let sample = sink.pull_sample().map_err(|_| gstreamer::FlowError::Eos)?;
                    let now = Instant::now();
                    {
                        // At most 2 frames a second, even if the source ignores max-framerate.
                        let mut last = kept.lock().unwrap();
                        if last.is_some_and(|l: Instant| now.duration_since(l) < Duration::from_millis(480)) {
                            return Ok(gstreamer::FlowSuccess::Ok);
                        }
                        *last = Some(now);
                    }
                    if let Some(image) = sample_to_rgb(&sample) {
                        drop(sample); // release PipeWire's buffer before the slower work
                        *slot.lock().unwrap() = Some(Latest::new(image, now));
                    }
                    Ok(gstreamer::FlowSuccess::Ok)
                })
                .build(),
        );
        pipeline.set_state(gstreamer::State::Playing).map_err(|e| format!("cannot start the frame pipeline: {e}"))?;
        let stop = Arc::new(AtomicBool::new(false));
        watch_bus(&pipeline, node_id, stop.clone());
        Ok(Box::new(GstFeed { pipeline, stop, _fd: fd }))
    }
}

/// The sample's frame as RGB (copied; the sample can be dropped after).
fn sample_to_rgb(sample: &gstreamer::Sample) -> Option<RgbImage> {
    use gstreamer_video::prelude::*;
    use gstreamer_video::{VideoFormat, VideoFrameRef, VideoInfo};
    let info = VideoInfo::from_caps(sample.caps()?).ok()?;
    let format = match info.format() {
        VideoFormat::Bgrx | VideoFormat::Bgra => PixelFormat::Bgrx,
        VideoFormat::Rgbx | VideoFormat::Rgba => PixelFormat::Rgbx,
        VideoFormat::Xrgb | VideoFormat::Argb => PixelFormat::Xrgb,
        VideoFormat::Xbgr | VideoFormat::Abgr => PixelFormat::Xbgr,
        VideoFormat::Bgr => PixelFormat::Bgr,
        VideoFormat::Rgb => PixelFormat::Rgb,
        _ => return None,
    };
    let frame = VideoFrameRef::from_buffer_ref_readable(sample.buffer()?, &info).ok()?;
    let stride = *frame.plane_stride().first()? as usize;
    to_rgb(format, frame.width(), frame.height(), stride, frame.plane_data(0).ok()?)
}

/// Logs the pipeline's errors (a stuck feed shows as an old "last frame" on the Screens page;
/// there are no automatic restarts).
fn watch_bus(pipeline: &gstreamer::Pipeline, node_id: u32, stop: Arc<AtomicBool>) {
    use gstreamer::prelude::*;
    let Some(bus) = pipeline.bus() else { return };
    let _ = std::thread::Builder::new().name(format!("feed-{node_id}")).spawn(move || {
        use gstreamer::MessageView;
        while !stop.load(Ordering::Relaxed) {
            let Some(msg) = bus.timed_pop(gstreamer::ClockTime::from_seconds(1)) else { continue };
            match msg.view() {
                MessageView::Error(e) => eprintln!("vitality-client: screen feed {node_id}: {} ({:?})", e.error(), e.debug()),
                MessageView::Eos(_) => {
                    eprintln!("vitality-client: screen feed {node_id} ended");
                    return;
                }
                _ => {}
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_each_packed_format_and_respects_stride() {
        // One row of two pixels: red then blue, each format's byte order, 2 bytes of padding.
        let cases: [(PixelFormat, [u8; 4], [u8; 4]); 4] = [
            (PixelFormat::Bgrx, [0, 0, 255, 0], [255, 0, 0, 0]),
            (PixelFormat::Rgbx, [255, 0, 0, 0], [0, 0, 255, 0]),
            (PixelFormat::Xrgb, [0, 255, 0, 0], [0, 0, 0, 255]),
            (PixelFormat::Xbgr, [0, 0, 0, 255], [0, 255, 0, 0]),
        ];
        for (format, red, blue) in cases {
            let mut data = vec![];
            data.extend_from_slice(&red);
            data.extend_from_slice(&blue);
            data.extend_from_slice(&[9, 9]); // padding to the stride
            data.extend_from_slice(&red);
            data.extend_from_slice(&blue);
            let img = to_rgb(format, 2, 2, 10, &data).unwrap();
            assert_eq!(img.get_pixel(0, 1).0, [255, 0, 0], "{format:?}");
            assert_eq!(img.get_pixel(1, 1).0, [0, 0, 255], "{format:?}");
        }
        let bgr = to_rgb(PixelFormat::Bgr, 1, 1, 3, &[1, 2, 3]).unwrap();
        assert_eq!(bgr.get_pixel(0, 0).0, [3, 2, 1]);
        let rgb = to_rgb(PixelFormat::Rgb, 1, 1, 4, &[1, 2, 3]).unwrap();
        assert_eq!(rgb.get_pixel(0, 0).0, [1, 2, 3]);
    }

    #[test]
    fn short_data_is_refused() {
        assert!(to_rgb(PixelFormat::Bgrx, 4, 4, 16, &[0; 20]).is_none());
        assert!(to_rgb(PixelFormat::Bgrx, 4, 1, 8, &[0; 16]).is_none()); // stride < width
    }

    #[test]
    fn the_pipeline_is_the_specs() {
        let d = pipeline_description(7, 42);
        assert!(d.starts_with("pipewiresrc fd=7 path=42 always-copy=true keepalive-time=1000 ! video/x-raw,max-framerate=2/1,format={BGRx,"));
        assert!(d.ends_with("! appsink name=sink max-buffers=1 drop=true sync=false"));
    }

    #[test]
    fn the_pipeline_parses_when_gstreamer_and_pipewiresrc_are_installed() {
        use gstreamer::prelude::*;
        if gstreamer::init().is_err() || gstreamer::ElementFactory::find("pipewiresrc").is_none() {
            eprintln!("skipped: no GStreamer pipewiresrc here");
            return;
        }
        let p = gstreamer::parse::launch(&pipeline_description(0, 1)).expect("the description parses");
        let _ = p.set_state(gstreamer::State::Null);
    }
}
