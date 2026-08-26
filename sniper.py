"""
Generates the in-browser sniper.

A hold placed by this server is useless to you: ForeUp scopes a pending
reservation to the session that created it, so a server-side hold reads as
"Sorry, that tee time is no longer available" in your own browser — verified
against the live API, with and without your login.

The hold therefore has to happen in *your* session. This module builds a
self-contained script you paste into the console on the ForeUp booking page.
It runs in your tab, with your cookies, so the pending reservation it creates
is yours: it lands in localStorage exactly where ForeUp's own code looks for
it, and the booking modal picks it up with a countdown.

It is self-contained on purpose — a script served from this app and fetched
cross-origin would be blocked by CORS, and pasting the whole thing avoids
needing any permission from foreupsoftware.com.
"""

import json


def claim_js(reservation_id: str, phpsessid: str, slot: dict,
             players: int, holes: int = 18) -> str:
    """
    Build the snippet that hands a server-side hold to the user's browser.

    ForeUp's session cookie is not HttpOnly:

        Set-Cookie: PHPSESSID=...; path=/; samesite=None; secure

    so a script running on foreupsoftware.com can adopt the server's session.
    ForeUp's own client tracks a pending reservation in localStorage rather than
    asking the server for it, so the second half writes that entry in the exact
    shape `PendingReservation.store()` uses — otherwise the booking modal treats
    the slot as gone.

    SECURITY: the session id is a credential. While the server is logged in it
    grants access to that ForeUp account, so a claim snippet is short-lived and
    should not be pasted anywhere public.
    """
    payload = {
        "sid": phpsessid,
        "res": {
            "reservation": {**slot, "reservation_id": reservation_id,
                            "players": players, "holes": holes},
            "date_reserved": None,        # filled in by the snippet, client-side
        },
        "when": slot.get("time", ""),
    }
    return _CLAIM_TEMPLATE.replace("__CLAIM__", json.dumps(payload))


_CLAIM_TEMPLATE = r"""
(() => {
  const C = __CLAIM__;
  if (!location.hostname.includes('foreupsoftware.com')) {
    alert('Run this on the ForeUp booking page first.');
    return;
  }
  // Adopt the session that owns the hold.
  document.cookie = 'PHPSESSID=' + C.sid + '; path=/; secure; samesite=None';
  // Put the reservation where ForeUp's own code looks for it.
  const res = C.res; res.date_reserved = new Date();
  localStorage.setItem('pending_reservation', JSON.stringify(res));
  console.log('[claim] session adopted, reservation stored:', C.res.reservation.reservation_id);
  alert('Claimed ' + C.when + '.\nReloading — your held time should appear in the cart.');
  location.reload();
})();
"""


def sniper_js(job: dict, release_utc_iso: str | None = None) -> str:
    """
    Build the console script for one watch.

    `release_utc_iso` arms it to start bursting shortly before that instant;
    without it the script starts polling immediately.
    """
    cfg = {
        "courseId":     str(job.get("course_id", "")),
        "scheduleId":   str(job.get("schedule_id", "")),
        "bookingClass": str(job.get("booking_class", "")),
        "date":         job.get("target_date", ""),      # MM-DD-YYYY
        "timeFrom":     job.get("time_from", "00:00"),
        "timeTo":       job.get("time_to", "23:59"),
        "players":      int(job.get("players", 2)),
        "holes":        int(job.get("holes", 18)),
        "releaseUtc":   release_utc_iso or "",
        "leadMs":       20000,
        "intervalMs":   250,
        "windowMs":     120000,
    }
    return _TEMPLATE.replace("__CONFIG__", json.dumps(cfg))


# The `players` sent to /times is deliberately the real group size: ForeUp
# filters by allowed_group_sizes, so asking for 1 at a course that bars singles
# returns nothing. The hold body must be form-encoded and carry exactly the
# fields ForeUp's own bundle picks — see HOLD_FIELDS in foreup_client.py.
_TEMPLATE = r"""
(async () => {
  const CFG = __CONFIG__;
  const HOLD_FIELDS = ['time','holes','players','carts','schedule_id',
    'teesheet_side_id','course_id','booking_class_id','duration','foreup_discount',
    'foreup_trade_discount_rate','trade_min_players','cart_fee','cart_fee_tax',
    'green_fee','green_fee_tax'];

  if (!location.hostname.includes('foreupsoftware.com')) {
    alert('Run this on the ForeUp booking page:\n' +
          'https://foreupsoftware.com/index.php/booking/' + CFG.courseId);
    return;
  }

  // ── status overlay ────────────────────────────────────────────────────────
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:99999;' +
    'background:#0f172a;color:#e2e8f0;font:13px/1.5 system-ui,sans-serif;' +
    'padding:14px 16px;border-radius:10px;max-width:340px;box-shadow:0 8px 24px rgba(0,0,0,.4)';
  document.body.appendChild(box);
  let attempts = 0;
  const say = (html) => { box.innerHTML = html; console.log('[sniper]', box.innerText); };

  const toMin = (t) => { const [h,m] = t.split(':').map(Number); return h*60+m; };
  const fromMin = toMin(CFG.timeFrom), toMinutes = toMin(CFG.timeTo);

  const slotMinutes = (s) => {
    const m = String(s || '').match(/(\d{1,2}):(\d{2})/g);
    if (!m) return null;
    const parts = String(s).split(/[ T]/);
    const hhmm = (parts[1] || parts[0]).match(/^(\d{1,2}):(\d{2})/);
    return hhmm ? (+hhmm[1]) * 60 + (+hhmm[2]) : null;
  };

  async function fetchTimes() {
    const q = new URLSearchParams({
      time: 'all', date: CFG.date, holes: String(CFG.holes),
      players: String(CFG.players), booking_class: CFG.bookingClass,
      schedule_id: CFG.scheduleId, 'schedule_ids[]': CFG.scheduleId,
      specials_only: '0'
    });
    const r = await fetch('/index.php/api/booking/times?' + q, {
      headers: {'X-Requested-With':'XMLHttpRequest','Api-Key':'no_limits',
                'X-Fu-Golfer-Location':'foreup'},
      credentials: 'same-origin'
    });
    const data = await r.json();
    if (!Array.isArray(data)) return [];
    return data.filter(s => {
      const mins = slotMinutes(s.time);
      return mins !== null && mins >= fromMin && mins <= toMinutes;
    });
  }

  async function hold(slot) {
    const src = Object.assign({}, slot,
      {players: CFG.players, holes: CFG.holes, carts: 0, duration: 1});
    const body = new URLSearchParams();
    for (const f of HOLD_FIELDS) {
      let v = src[f];
      if (v === undefined || v === null) continue;
      if (typeof v === 'boolean') v = v ? 'true' : 'false';
      body.append(f, v);
    }
    const r = await fetch('/index.php/api/booking/pending_reservation', {
      method: 'POST', body,
      headers: {'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
                'X-Requested-With':'XMLHttpRequest','Api-Key':'no_limits'},
      credentials: 'same-origin'
    });
    const text = await r.text();
    let data = {}; try { data = JSON.parse(text); } catch (e) {}
    if (!r.ok || data.success === false) {
      throw new Error(data.message || ('HTTP ' + r.status + ' ' + text.slice(0,120)));
    }
    return data;
  }

  // ── wait for the release moment ───────────────────────────────────────────
  if (CFG.releaseUtc) {
    const start = new Date(CFG.releaseUtc).getTime() - CFG.leadMs;
    while (Date.now() < start) {
      const s = Math.round((start - Date.now()) / 1000);
      say(`🎯 <b>Sniper armed</b><br>Bursting in ${Math.floor(s/60)}m ${s%60}s` +
          `<br><span style="color:#94a3b8">${CFG.date} ${CFG.timeFrom}–${CFG.timeTo}` +
          `, ${CFG.players} players</span><br>` +
          `<span style="color:#f87171">Keep this tab open.</span>`);
      await new Promise(r => setTimeout(r, 1000));
    }
  }

  // ── burst ─────────────────────────────────────────────────────────────────
  const deadline = Date.now() + CFG.windowMs;
  say('🔥 <b>Bursting…</b>');
  while (Date.now() < deadline) {
    attempts++;
    let times = [];
    try { times = await fetchTimes(); } catch (e) { /* expected while it flips */ }

    if (times.length) {
      const slot = times[0];
      say(`🎯 Found ${times.length} — holding <b>${slot.time}</b>…`);
      try {
        const res = await hold(slot);
        // Store it exactly where ForeUp's own code looks, so the booking modal
        // shows the countdown instead of treating the slot as gone.
        localStorage.setItem('pending_reservation', JSON.stringify({
          reservation: Object.assign({}, slot,
            {reservation_id: res.reservation_id, players: CFG.players, holes: CFG.holes}),
          date_reserved: new Date()
        }));
        say(`✅ <b>HELD ${slot.time}</b><br>reservation ${res.reservation_id}` +
            `<br>Reloading so you can confirm — <b>you have ~5 minutes</b>.`);
        try { new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=').play(); } catch(e){}
        setTimeout(() => location.reload(), 2500);
        return;
      } catch (e) {
        say(`⚠️ Hold failed: ${e.message}<br>Retrying…`);
      }
    } else if (attempts % 8 === 0) {
      say(`⏳ Waiting for the sheet… <b>${attempts}</b> checks`);
    }
    await new Promise(r => setTimeout(r, CFG.intervalMs));
  }
  say(`❌ Window closed after ${attempts} checks — nothing in ` +
      `${CFG.timeFrom}–${CFG.timeTo}.`);
})();
"""
