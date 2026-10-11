# Per-pin Loogle

One frozen Loogle index per corpus pin (TauCeti sha + Mathlib rev + toolchain), each on its own unix socket,
like Lean Finder and LeanExplore. The legacy single-directory units (`tauceti-loogle.service`, port 8088,
`$XDG_RUNTIME_DIR/tauceti-loogle/loogle.sock`) are untouched; the bench prefers the per-pin directory when present.

## Layout (per `<pin>`, e.g. `b3-11bc13335fb9`)
- `~/.local/share/tauceti-loogle/<pin>/TauCeti` checkout at the pin plus generated `TauCeti/LoogleAll.lean`
- `~/.local/share/tauceti-loogle/<pin>/loogle` nomeata/loogle at `9f11169a` built with the pin's toolchain
- `~/.local/share/tauceti-loogle/<pin>/MANIFEST.json` (also copied to `manifests/<pin>.json`), `timings.json`
- `~/.local/state/tauceti-loogle/<pin>/TauCeti.LoogleAll.loogle-index`
- `~/.config/tauceti-loogle/<pin>.env`; units `tauceti-loogle@<pin>.service`, `tauceti-loogle-proxy@<pin>.{socket,service}`
- socket `$XDG_RUNTIME_DIR/tauceti-loogle/<pin>/loogle.sock`; ports b2 8086, b3 8087 (legacy 8088)
- `server.py` is a copy of upstream's with `--manifest` and `GET /health` ({ok, tool, pin, manifest}); `/json` unchanged.

## Rebuild / new pin
1. Extraction manifest must exist: `~/.local/share/tauceti-index/<pin>/MANIFEST.json`.
2. `scripts/build-pin.sh <pin>` (idempotent per stage; `SEED_CHECKOUT=<built checkout at the pin>` copies instead of cloning;
   delete a key from `timings.json` to redo that stage). Run detached, log to `~/.local/share/tauceti-loogle/logs/`.
3. `scripts/install-pin-units.sh <pin>:<port>` (writes env, templates, enables both units).
4. Check `curl --unix-socket $XDG_RUNTIME_DIR/tauceti-loogle/<pin>/loogle.sock http://localhost/health`.
Note: the aggregate module needs a module docstring before `set_option linter.style.longFile 0`
(the Mathlib header linter is an error on rc2/rc3).

## Measured (128 cores, 2026-10-10)
| stage (s) | b2 (rc2) | b3 (rc3) |
|---|---|---|
| checkout / copy | 8.7 (clone --reference) | 71.2 (cp -a of a prebuilt 22 GB checkout) |
| loogle build | 12.7 | 10.9 |
| TauCeti build (cache get + LoogleAll) | 634.4 (cold, 4506 modules) | 27.8 (checkout was already built) |
| index write | 173.3 | 285.9 |
| index size | 457 MB | 515 MB |
| modules imported | 4506 | 7783 |
Serving RSS (cgroup MemoryCurrent, 2 workers): b2 about 2.1 GB, b3 about 2.5 GB.
