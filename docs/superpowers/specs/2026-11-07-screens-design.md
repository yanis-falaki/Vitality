# Vitality — Several monitors (screens)

**Date:** 2026-11-07
**Status:** Agreed with the owner in conversation; implemented on branch `screens`.

## Rules

1. **One screen-share per monitor, portably.** Client config `screens` (integer, default 1) is
   the number of xdg-desktop-portal ScreenCast sessions the daemon opens, one after the other.
   Hyprland's picker shares one monitor per session (so `screens = 2` for two monitors); GNOME
   and KDE can share several in one (keep `screens = 1`, select all). Every stream of every
   session is one of the machine's screens. Each session has its own restore token, saved as
   `restore_tokens` in the state file; an older file's single `restore_token` loads as session
   1's, and session 1's token is still written as `restore_token` for older daemons. No `grim`,
   nothing compositor-specific for capture.
2. **One image per screen, named by position.** Streams sorted by (x, y); ties keep session
   order. 1 screen: no name, exactly as before. 2: `left`, `right`. 3: `left`, `middle`,
   `right`. More: `screen-1`, ... left to right. Same-second images get the usual `-<n>` suffix.
3. **Which screens a shot saves** (`client/.../policy/screens.py`, pure): the first shot of an
   interaction and a manual capture save every screen; later shots save each screen whose content
   changed since that screen's own last saved image, plus the focused screen when a focus hint
   names one; if none qualifies, that moment saves nothing (the policy's timing is unchanged).
   One screen is always saved. "Changed": 64×36 grayscale thumbnails; a thumbnail pixel changed
   if it moved by more than 16 gray levels; a screen changed if more than 12 of its 2304 pixels
   did (~0.5%). Cursor blinks, clock digits and the mouse pointer stay below that; scrolling, a
   new message line or a window switch are well above it.
4. **Optional focus hint** (`FocusHint.focused_screen(screens) -> name | None`): on Hyprland
   (`HYPRLAND_INSTANCE_SIGNATURE` set, `hyprctl` on PATH) `hyprctl monitors -j`, matching the
   focused monitor to a stream by position, then by size; anything unexpected gives no hint.
   Elsewhere there is no hint.
5. **Protocol:** `Shot` gains optional `screen` and `focused` (left out when None, so
   one-screen interactions are byte-for-byte as before and old servers still accept them).
6. **Observer:** the line before each image adds `· left screen · focused` / `· right screen ·
   other` / `· right screen` (no hint); the prompt says images are per screen, the focused one is
   what the owner worked on, the others are context.

**Deployment:** server and observer before any client that captures more than one monitor (an
old server rejects the new fields; a one-screen client sends none of them).
