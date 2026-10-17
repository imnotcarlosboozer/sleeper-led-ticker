# Sleeper Injury LED Ticker

Scrolls fantasy football injury news from the Sleeper API across HUB75 RGB panels
on a Raspberry Pi 3 with an Adafruit RGB Matrix Bonnet/HAT.

## Deploy
From your computer (replace `<user>` and `<pi-ip>` with your Pi's SSH user and IP address):

    scp -r sleeper-led-ticker <user>@<pi-ip>:~/
    ssh <user>@<pi-ip>
    cd ~/sleeper-led-ticker && sudo ./install.sh && sudo reboot

It starts on boot. Useful commands:

    journalctl -u sleeper-ticker -f          # logs
    sudo systemctl restart sleeper-ticker    # after editing config.json
    sudo systemctl stop sleeper-ticker       # stop it
    python3 ticker.py --preview              # print ticker text in terminal

## config.json
- `panel.rows` / `cols`: size of ONE panel (e.g. 32x64, 16x32, 32x32)
- `panel.chain_length`: number of panels daisy-chained
- `panel.hardware_mapping`: `adafruit-hat`, or `adafruit-hat-pwm` if you soldered the GPIO4-GPIO18 jumper
- `panel.gpio_slowdown`: raise to 3-4 if you see garbage/flicker, lower to 1 if it's dim/slow
- `display.scroll_pixels_per_second`: scroll speed
- `data.sort`: `recent` (newest news first) or `rank` (most popular players first)
- `data.max_search_rank`: lower = only bigger-name players
- `data.refresh_hours`: hours between Sleeper fetches (2). Sleeper asks for at most once per day; 2h catches practice reports and game-day inactives

## Trend icons
Each fetch is compared with the last one (`history.json`). Status order, best to worst:
healthy, Questionable, Doubtful, Out, IR/PUP/Sus/other. A move toward healthy draws a green
up-chart icon; a move away draws a red down-chart icon. Changed players lead the ticker as
`NAME POS TEAM OLD > NEW` for `data.trend_hours` (default 72). Recovered players show as ACTIVE.

Show sample trends on the panel:

    sudo systemctl stop sleeper-ticker
    sudo python3 ticker.py --data demo_injuries.json     # Ctrl+C to quit
    sudo systemctl start sleeper-ticker

Sleeper keeps no status history, so new trends normally need two fetches. To get arrows
immediately (first install, or after wiping history.json), seed from each player's last game:

    sudo python3 seed_from_last_game.py && sudo systemctl restart sleeper-ticker
