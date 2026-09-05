# AGENTS.md

Instructions for humans and AI agents working in this repo.

## What this is

urbtop is a single-process dashboard for one running Urbit ship. Three files
matter:

- `urbtop.py`: collector threads plus a stdlib HTTP server. Fast tick (2 s),
  slow sweep (12 s), and a `|mass` loop. Serves `/`, `/api/state`, `POST /api/mass`.
- `urbtop.html`: the page. Vanilla JS, no build step, polls `/api/state`.
- `noun.py`: jam/cue, newt framing, `@p`/`@da`/`@uv`, treap walking.
  `python3 noun.py` is its self-test.

No dependencies beyond Python 3.10+ stdlib. Keep it that way.

## The one rule that matters

**Never abandon a conn.sock request.** On vere 4.6 a client that disconnects
while the ship still owes it a reply can segfault the king
(urbit/vere#1100). `Ship` in `urbtop.py` exists to make that impossible under
normal operation: one persistent connection, replies matched by request id,
timed-out requests left owed, and a drain on SIGTERM. Do not add code that
opens a socket per request, closes the socket on a timeout, or exits without
`Ship.drain()`. Do not `kill -9` a running urbtop against a ship you care about.

Corollary: **never peek a noun you have not sized on a fake ship first.** Some
scries are bytes on a fake ship and hundreds of megabytes on a real one
(eyre `channel-state`, anything `//whey`, clay `domes`/`sweep`). The serf jams
the whole reply before it is sent, so a huge peek also stalls the ship.

## Adding a metric

1. Find the scry in the vane source (`sys/vane/*.hoon`, arm `+scry`) or the
   runtime peel in vere's `conn.c`. Cares and paths differ between kelvins;
   test against the base version you actually run.
2. Probe it once with `Ship.peek(view, desk, *spur)` on a **fake ship** and
   check both the shape and `len(jam_bytes(...))`. Gall vane scries
   (`%ge`, `%gu`, `%gd`, `%gf`) need the `/$` spur, which `peek` sends when
   `spur` is empty.
3. Decode it in a small function next to the other decoders; put it in
   `fast_tick` only if it is cheap and changes often, otherwise `slow_tick`.
4. Cap anything unbounded before it goes into `state` (see timers and peers).
5. Render it in `urbtop.html` inside a `panel(...)` call. Use `table(...)` for
   tabular data so it sorts. Panels repaint only when their HTML changes.

`panel(id, cls, html)` takes an html string starting with `<h2>`; everything
after the heading is wrapped in a `.body` block automatically. `cls` is only a
default width (`two`, `wide`, or empty) that the user can override by dragging.

Each panel keeps its own `<section>` across repaints, keyed by id, so layout
and scroll positions survive. Anything you attach to the section itself rather
than to its innerHTML must be re-attached in `decorate()`, which runs after
every repaint. Layout, sort, and mass-tree state persist under the
`urbtop.layout`, `urbtop.sort`, and `urbtop.open` localStorage keys; changing
their shape should tolerate stale values (see `lsGet`).

## Running against a real ship

- Use `urbtop.service` (systemd user unit) so stops are SIGTERM with a long
  `TimeoutStopSec`. Bind to localhost or a private address; there is no auth.
- Set `--mass-interval` with the loom size in mind: `|mass` pauses the ship
  for roughly a second per 2 GB of loom.
- The console panel needs the ship's stderr in a file; `tmux pipe-pane` on the
  ship's pane is the simplest way.

## Things that are deliberately not here

No auth, no TLS, no history beyond the in-memory sparklines, no per-peer detail
past the first 300 peers, no channel counts (see the rule above). Add them when
a real need shows up, not before.
