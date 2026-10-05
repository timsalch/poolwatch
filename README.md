# poolwatch

Pool monitoring built on Roboflow: Ring camera frames go through a Roboflow
Workflow, which drives two things:

1. **Swimmer alerts:** push a notification (with the frame) when someone is in
   the water during quiet hours, or in the water with nobody on the deck.
2. **Debris-driven filter runtime:** when floating debris shows up, boost the
   Hayward OmniLogic variable-speed filter pump so the skimmer catches it before
   it sinks, then hand control back to the normal schedule.

> **Safety:** this is an awareness layer, not drowning protection. Ring clips
> arrive with seconds-to-minutes of delay. Keep the fence, self-latching gate,
> and a dedicated pool alarm.

## How it works

```
Ring (motion clips + periodic snapshots)
  -> frames  ->  Roboflow Workflow (person + custom debris model)
  -> safety rules  -> ntfy push
  -> debris score  -> PumpPolicy -> OmniLogic (local UDP API)
  -> data/decisions.jsonl  (every decision, for tuning and a future scheduling model)
```

The pump policy (`poolwatch/debris.py`) is pure logic with these rules:

- It requires N consecutive high readings before boosting, which filters out glare.
- Each boost runs for a fixed time, can be extended while debris remains (up to a limit), and is followed by a cooldown.
- It never boosts while someone is in the water, and ends an active boost if a swimmer appears.
- It never boosts during no-boost hours, such as overnight.
- It enforces a daily boost-minute budget.
- Ending a boost sends OmniLogic's **RestoreIdleState**, which returns control to the programmed schedule.

## Setup

Requires **Python 3.13**. The OmniLogic library needs 3.13 or newer, and
Roboflow's `inference-sdk` doesn't support 3.14 yet.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
cp config.example.toml config.toml        # then edit
export ROBOFLOW_API_KEY=...
```

1. **Ring login (once):** `poolwatch ring-login` handles 2FA and saves a
   refresh token to `.ring_token.json`.
2. **Find the pump:** `poolwatch pump-info` lists the filters on the controller.
   Copy the exact name into `omnilogic.filter_name`.
3. **Collect training data:** run `poolwatch collect --hours 336` for about two
   weeks. It saves snapshots to `data/snapshots/` and motion-clip frames to
   `data/frames/`. Frame extraction needs `ffmpeg` on your PATH.
4. **Train in Roboflow:** upload the frames, label `leaf`, `debris`, and `bug`.
   Include plenty of clean, sunny frames so the model learns glare is not debris.
   Then train.
5. **Build one Workflow** with two branches: a pretrained person detector
   (COCO is fine) and your debris model. The parser picks up every box,
   whatever you name the outputs.
6. **Draw zones:** take one snapshot and put pixel polygons for `water`,
   `deck`, and (optionally) `gate` into `config.toml`.
7. **Sanity check:** `poolwatch detect data/snapshots/<file>.jpg`
8. **Run:** start with `poolwatch run --dry-run`, then watch
   `data/decisions.jsonl` for a few days. Once the decisions look right, set
   `omnilogic.dry_run = false`.

For push alerts, install the ntfy app and set `notify.ntfy_topic` to a long
random string.

## Tests

```bash
pytest
```

The tests cover zones, Workflow output parsing, config parsing, the safety
rules and alert throttling, the full pump policy state machine, the pipeline
end to end with fake detector and pump, and the OmniLogic adapter. There is
also a guard test that fails if `python-omnilogic-local` renames a method the
adapter relies on.

## Verify on real hardware

These parts are written against the libraries' APIs but haven't been run
against your devices yet:

- **`ring_source.py`** uses the unofficial `ring_doorbell` package. Snapshots
  only work on wired cameras.
- **`OmniLogicPump.restore_schedule`** calls the library's API-layer
  `async_restore_idle_state()`, because it isn't wrapped by the high-level
  class yet. Test what happens when a scheduled program starts during a boost.

## Next

- Add wind speed from a weather API to the decision log, then learn when to
  pre-schedule boosts before the afternoon leaf drop.
- Move inference local with `inference server start` and set
  `roboflow.api_url = "http://localhost:9001"`.
