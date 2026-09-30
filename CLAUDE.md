# DRIFTS

Project context, known defects and landmines are in **[AGENTS.md](AGENTS.md)**.
Read it before changing anything — several of the constraints there are not
inferable from the code, and at least two look like working code but are not.

Quick checks before any commit:

```bash
cd esp32/test && make          # host tests, 28 assertions, no hardware
python3 hardware/check_pins.py # pin docs vs config.h
cd raspberry && python3 -m pytest -q
```
