# Train internet

See how well the internet works during a train trip: **where it drops, for how long,
and how fast it is**, along the kilometres of the trip. It uses very little data
(about 5 MB per hour), so it doesn't slow down your own browsing.

Runs on a **Mac** or an **Android phone**. A phone works best, because it knows where the
train is (GPS) and also records its own mobile signal.

> **Status: not tested on a train yet.** This is an early version, tested at home with
> simulated trips. Expect fixes after the first real journeys.

## How to use it

1. Type `trainnet`. A page opens in your browser.
2. When the train leaves, press **Start recording**. You can close the page or lock the
   phone: it keeps recording.
3. On arrival, press **Stop recording**. The result appears:

   - how much of the time the internet was available,
   - each outage: when, how long, and at which kilometre,
   - a strip along the trip, coloured green (online), yellow (patchy), red (no internet),
     with the response time underneath.

Every trip stays in the list, so you can compare journeys on the same line.

## Install

### Android phone

1. Install **Termux** and **Termux:API** from [F-Droid](https://f-droid.org). Take both
   from F-Droid: the Play Store versions don't work together with them.
2. Open Termux and type:
   ```sh
   pkg install -y git
   git clone https://github.com/tjyderrien/internet-train-coverage
   sh internet-train-coverage/android/install-termux.sh
   ```
   Accept the permission pop-ups (location, storage).
3. In Android settings, give **Termux:API** location access **"Allow all the time"**,
   and set the battery mode of **Termux** and **Termux:API** to **Unrestricted**.
   Otherwise Android stops the recording when the screen is off.

Optional: the **Termux:Widget** app adds a home-screen button that starts it.

### Mac

```sh
git clone https://github.com/tjyderrien/internet-train-coverage
cd internet-train-coverage
python3 -m venv .venv && .venv/bin/pip install .
.venv/bin/trainnet
```

A Mac has no GPS, so its results are shown over time only, unless the train's Wi-Fi
portal publishes the position: see **Options** on the start page.

### Update

```sh
cd internet-train-coverage && git pull && pip install .     # on the Mac: .venv/bin/pip
```

## Good to know

- **What it measures:** every 2 seconds, whether a few well-known servers answer and how
  fast. Every 2 minutes, a small 100 kB download. Also the Wi-Fi signal and, on a phone,
  the mobile signal.
- **Train Wi-Fi vs. outside coverage:** the train's Wi-Fi relies on the train's own mobile
  connection. On a phone you can see both: if the internet drops while the phone's own
  signal stays good, the train's connection is the problem. If both drop, it's coverage.
- **Privacy:** the data stays on your device. The page is only reachable from the device
  itself, not by other passengers.
- **Moving phone trips to the Mac:** at the end of each trip the phone saves a copy in
  `Documents/trainnet/trainnet.sqlite`. On the Mac, `trainnet merge <that file>` adds the
  trips to the Mac's list.

## More

Technical details (what each test does, command-line tools, comparing trips against
antenna positions and weather, data format for building your own frontend) are in
[docs/ADVANCED.md](docs/ADVANCED.md).

MIT licence.
