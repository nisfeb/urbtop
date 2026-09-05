# urbtop

A bashtop / netdata style dashboard for a running Urbit ship. One Python file,
stdlib only, plus a single HTML page. It shows:

- **runtime**: king and serf CPU, RSS, threads, fds, page faults, context
  switches, event number and events/s, serf uptime, snapshot age
- **loom**: mapped size, free/idle, marked bytes, host load and memory
- **pier**: event log size and epochs, snapshot size and age, disk free,
  ames/http ports, sponsor chain, turf, terminal sessions
- **behn**: every pending timer with due-in countdown, wire and full duct
- **ames**: every peer with state, last contact, send/receive flow counts,
  corked/closing flows, rift, protocol version, snub list, chums
- **clay**: every desk with zest, revision and commit time, kelvin(s), hash,
  sync source, docket title/version, bill
- **gall**: every agent per desk, live or suspended, subscription nonce
- **spider**: running thread ids
- **eyre**: bindings with owning agent, open http connections, channels with
  subscription counts, CORS registry, host, ports, domains
- **|mass**: the full memory report as a collapsible tree with bars
- **ship console** tail (optional, see below)
- the raw vere `/info` tree

## How it reads the ship

Everything comes through the pier's `.urb/conn.sock`:

- `%peek` = khan namespace reads (scries). These do not create events and do
  not touch the event log. Each takes a few milliseconds in the serf.
- `%peel` = runtime metrics (`/info`, `/v`, `/who`, `/port/*`, `/mass`,
  `/quic`). `/mass` is the real `|mass` report as data; it walks the loom and
  pauses the ship for about a second, so it runs on a slow interval (default
  5 minutes) or when you press the button. `/quic` prints two lines on the
  ship console, so it only runs alongside `|mass`.

Process and disk numbers come from `/proc` and the pier directory.
Nothing goes through the dojo, %lens, or eyre.

## Warning: this can crash your ship

Read this before pointing urbtop at a ship you care about.

On vere 4.6, if a conn.sock client disconnects while the ship still owes it a
reply, the king prints `newt: write failed broken pipe` / `conn: moor bail -32
broken pipe` and can segfault (`loom: external fault` in `u3_king_commence`).
The event log is not damaged, but the ship goes down until someone restarts
it. The bug is in vere, not in the ship's state, and is reported with a
reproducer at **https://github.com/urbit/vere/issues/1100** (same class as the
older, unresolved #490). Until it is fixed there, any conn.sock client is a
loaded gun, and urbtop is a conn.sock client.

While developing this tool it took a production ship down twice: once from a
probe killed mid-reply, once from `pkill` of urbtop while an ordinary reply was
in flight.

### Precautions urbtop takes

- One persistent conn.sock connection for the life of the process, with
  replies matched by request id. It never opens and closes a socket per
  request, so there is no routine disconnect for the bug to bite on.
- A slow reply never causes a disconnect. A request that times out on our side
  is left owed and consumed when it finally arrives.
- On SIGTERM or SIGINT it stops issuing requests, waits for every outstanding
  reply (up to 15 minutes; `|mass` can be slow), and only then closes the
  socket and exits.
- It never asks for nouns known to be enormous on real ships (eyre
  `channel-state`, anything `//whey`, clay `domes`/`sweep`), and caps timers
  and peers before they reach the browser.
- `urbtop.service` stops it with SIGTERM and a 15 minute grace period so a
  systemd stop is always an orderly drain.

### What the precautions do not cover

Be honest with yourself about this part. The drain only runs if urbtop gets a
chance to run it. These will still disconnect with replies owed and can still
crash the ship:

- `kill -9`, an OOM kill, or the host rebooting or losing power while a reply
  is in flight;
- urbtop itself crashing on an unexpected noun shape mid-request (it is
  wrapped in try/except per call, but a bug is a bug);
- any other conn.sock client on the same ship that does not follow these rules
  (this includes `click` scripts interrupted with Ctrl-C).

The window is small in steady state, a few milliseconds per request every two
seconds, and large during `|mass`, which can take seconds on a big loom. If a
crash would be worse for you than not having the dashboard, do not run it
against that ship, or run it only while you are watching. Test on a fake ship
first; the shapes and sizes of scry replies differ a lot between fake and real
ships.

## Running as a service

`urbtop.service` is a systemd user unit (edit PIER/LOG/flags at the top):

```
cp urbtop.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now urbtop
loginctl enable-linger $USER   # keep it running when you log out
```

## Run

```
python3 urbtop.py /path/to/pier
python3 urbtop.py /path/to/pier --port 9909 --mass-interval 300 --log /path/to/console.log
```

Open http://127.0.0.1:9909/. Use `--bind 0.0.0.0` to expose it (there is no
auth, put it behind something if you do). `--mass-interval 0` makes `|mass`
manual only.

## Console tail

Vere prints to the terminal it was started in, so to get the "ship console"
panel, tee that terminal into a file. If the ship runs in tmux:

```
tmux pipe-pane -t <session>:<window>.<pane> 'cat >> /path/to/console.log'
```

Or start vere with `2>>/path/to/console.log`, or point `--log` at the journal
if it runs under systemd (`journalctl -fu urbit -o cat >> file &`).

## Files

- `urbtop.py`: collector threads (fast tick 2s, slow sweep 12s, mass loop) and
  the HTTP server (`/` page, `/api/state` JSON, `POST /api/mass`).
- `urbtop.html`: the page. Polls `/api/state` every 2s, draws sparklines on
  canvas, keeps the mass tree fold state in localStorage.
- `noun.py`: jam/cue, newt framing over conn.sock, `@p`/`@da`/`@uv`, treap
  walking. `python3 noun.py` runs its self-check.
