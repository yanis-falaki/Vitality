//! The screen-share portal (xdg-desktop-portal ScreenCast): one session per screen.
//!
//! `Portal::open(token)` asks for one monitor: with a restore token it reopens a saved
//! permission (no dialog when the portal accepts it), without one it shows the portal's picker.
//! It returns the PipeWire node and remote fd to read frames from, the new restore token (to
//! save: tokens can be single-use), and `keep`, which holds the session open until dropped.
//! `XdgPortal` is the real one (ashpd); tests use their own `Portal`.

use std::any::Any;
use std::os::fd::OwnedFd;

use async_trait::async_trait;

/// An open screen-share.
pub struct Opened {
    pub node_id: u32,
    pub fd: OwnedFd,
    pub restore_token: Option<String>,
    /// Keeps the portal session alive; dropping it ends the share.
    pub keep: Box<dyn Any + Send + Sync>,
}

impl std::fmt::Debug for Opened {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Opened").field("node_id", &self.node_id).field("restore_token", &self.restore_token).finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PortalError {
    /// The owner cancelled the picker, or the portal refused the saved permission.
    Refused,
    /// No screen-share portal on the session bus.
    Unavailable(String),
    /// Anything else (said in the text).
    Failed(String),
}

impl std::fmt::Display for PortalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PortalError::Refused => f.write_str("the screen share was refused or cancelled"),
            PortalError::Unavailable(why) => write!(f, "the screen-share portal is not available: {why}"),
            PortalError::Failed(why) => write!(f, "the screen share failed: {why}"),
        }
    }
}

#[async_trait]
pub trait Portal: Send + Sync {
    async fn open(&self, token: Option<&str>) -> Result<Opened, PortalError>;
}

/// The real portal through ashpd: a monitor (not a window), one per session, the cursor drawn
/// into the frames, the permission kept until the owner revokes it.
pub struct XdgPortal;

#[async_trait]
impl Portal for XdgPortal {
    async fn open(&self, token: Option<&str>) -> Result<Opened, PortalError> {
        use ashpd::desktop::screencast::{CursorMode, Screencast, SelectSourcesOptions, SourceType};
        use ashpd::desktop::PersistMode;
        use ashpd::enumflags2::BitFlags;

        let proxy = Screencast::new().await.map_err(|e| PortalError::Unavailable(e.to_string()))?;
        let session = proxy.create_session(Default::default()).await.map_err(failed)?;
        let options = SelectSourcesOptions::default()
            .set_cursor_mode(CursorMode::Embedded)
            .set_sources(BitFlags::from_flag(SourceType::Monitor))
            .set_multiple(false)
            .set_persist_mode(PersistMode::ExplicitlyRevoked)
            .set_restore_token(token);
        proxy.select_sources(&session, options).await.map_err(failed)?.response().map_err(failed)?;
        let streams = proxy.start(&session, None, Default::default()).await.map_err(failed)?.response().map_err(failed)?;
        let stream = streams.streams().first().ok_or(PortalError::Refused)?;
        let node_id = stream.pipe_wire_node_id();
        let restore_token = streams.restore_token().map(str::to_string);
        let fd = proxy.open_pipe_wire_remote(&session, Default::default()).await.map_err(failed)?;
        Ok(Opened { node_id, fd, restore_token, keep: Box::new(Keep(Some((proxy, session)))) })
    }
}

fn failed(e: ashpd::Error) -> PortalError {
    use ashpd::desktop::ResponseError;
    match e {
        ashpd::Error::Response(ResponseError::Cancelled | ResponseError::Other) => PortalError::Refused,
        ashpd::Error::Portal(ashpd::PortalError::Cancelled(_) | ashpd::PortalError::NotAllowed(_)) => PortalError::Refused,
        other => PortalError::Failed(other.to_string()),
    }
}

type ScreencastSession = (ashpd::desktop::screencast::Screencast, ashpd::desktop::Session<ashpd::desktop::screencast::Screencast>);

/// Closes the portal session when dropped (on the tokio runtime, if there is one).
struct Keep(Option<ScreencastSession>);

impl Drop for Keep {
    fn drop(&mut self) {
        if let (Some((proxy, session)), Ok(rt)) = (self.0.take(), tokio::runtime::Handle::try_current()) {
            rt.spawn(async move {
                let _ = session.close().await;
                drop(proxy);
            });
        }
    }
}
