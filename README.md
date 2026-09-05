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

## Warning: conn.sock clients can crash the king

On vere 4.6, if a client disconnects while conn.c still owes it a reply, the
king prints `newt: write failed broken pipe` / `conn: moor bail -32 broken pipe`
and can segfault (`loom: external fault` in `u3_king_commence`). This took a
production ship down twice on 2026-09-05 during development: once from a probe
killed mid-reply (it had asked for eyre's `channel-state`, which is enormous on
a real ship), once from `pkill` of urbtop while a normal reply was in flight.
(Reported upstream; related to urbit/vere#490.)

urbtop therefore:

- keeps ONE conn.sock connection for the life of the process, with replies
  matched by request id, so it never closes a socket mid-conversation;
- never drops the connection on a slow reply: a timed-out request is simply
  ignored when its reply finally arrives;
- on SIGTERM/SIGINT waits for every outstanding reply (up to 15 minutes, `|mass`
  can be slow) before closing the socket and exiting;
- never requests known-huge nouns (`channel-state`, `//whey`, `domes`) and caps
  timers/peers before sending them to the browser.

Stop it with SIGTERM (`systemctl --user stop urbtop`, or `kill PID`), never
`kill -9`. A hard kill, an OOM kill, or a host crash while a reply is owed is
still a risk on a ship you care about; use `urbtop.service` so stops are
orderly. If you add a peek, measure its reply size on a fake ship first.

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
