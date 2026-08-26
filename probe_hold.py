"""
One-shot diagnostic: can this app place a ForeUp hold, and does it need a login?

Answers the question that decides how snipe mode should work. ForeUp's
`pending_reservation` endpoint is the "cart" — the thing that actually wins the
race when a tee sheet opens. It is NOT captcha-gated (the course has
force-recaptcha-on-tile-click disabled); only the final submit is. So if a hold
works, snipe mode can reliably claim a slot and leave you the last tap.

SAFETY
------
This places a REAL hold on a REAL tee sheet, then releases it immediately in a
finally block. It deliberately picks the LAST tee time of the day — the slot
least likely to be wanted by anyone else — and never completes a booking, so
nothing is charged and no round is reserved.

If the release ever fails, the script prints the reservation id and tells you
how to clear it by hand. Holds also lapse on their own after a few minutes.

USAGE
-----
    python probe_hold.py                  # anonymous (no login)
    python probe_hold.py --login          # uses FOREUP_EMAIL / FOREUP_PASSWORD
    python probe_hold.py --date 08-30-2026

Nothing here touches the database, so it runs fine without Postgres.
"""

import argparse
import json
import os
import sys
import time

import foreup_client as fc

COURSE_ID     = "19536"     # Grapevine Golf Course
SCHEDULE_ID   = "1832"
BOOKING_CLASS = "12800"     # Public


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="08-30-2026", help="MM-DD-YYYY")
    ap.add_argument("--login", action="store_true",
                    help="log in with FOREUP_EMAIL / FOREUP_PASSWORD first")
    ap.add_argument("--players", type=int, default=2)
    args = ap.parse_args()

    if args.login:
        email = os.environ.get("FOREUP_EMAIL", "")
        password = os.environ.get("FOREUP_PASSWORD", "")
        if not email or not password:
            print("--login needs FOREUP_EMAIL and FOREUP_PASSWORD in the environment.")
            return 2
        client = fc.ForeUpClient(email, password)
        print(f"Logging in as {email} …")
        try:
            client.login(COURSE_ID)
            print("   logged in OK\n")
        except Exception as e:
            print(f"   LOGIN FAILED: {e}\n")
            return 2
    else:
        client = fc.ForeUpClient()   # anonymous
        print("Running ANONYMOUSLY (no login)\n")

    # Find the last slot of the day — least desirable, least impact on others.
    times = client.fetch_tee_times(
        course_id=COURSE_ID, schedule_id=SCHEDULE_ID, date=args.date,
        time_from="00:00", time_to="23:59", players=args.players,
        booking_class=BOOKING_CLASS,
    )
    if not times:
        print(f"No open times on {args.date} — pick a date inside the booking window.")
        return 1

    slot = times[-1]
    print(f"Target slot : {slot['time']}   ${slot.get('green_fee')}   "
          f"{slot.get('available_spots')} spots   (LAST slot of the day)")
    print(f"Players     : {args.players}")
    print(f"ids         : course={slot.get('course_id')} schedule={slot.get('schedule_id')} "
          f"teesheet={slot.get('teesheet_id')} side={slot.get('teesheet_side_id')} "
          f"class={slot.get('booking_class_id')}")

    payload = fc.build_hold_payload(slot, players=args.players, holes=18)
    print("\nRequest body (form-encoded, matching ForeUp's own client):")
    for k in fc.HOLD_FIELDS:
        print(f"   {k:30} = {payload.get(k, '<omitted>')!r}")

    input("\nPress Enter to place the hold (Ctrl+C to abort) … ")

    res_id = None
    t0 = time.time()
    try:
        print("\n--- placing hold ---")
        data = client.hold(slot, players=args.players, holes=18)
        elapsed = (time.time() - t0) * 1000
        print(f"HOLD SUCCEEDED in {elapsed:.0f}ms  "
              f"(ForeUp holds it for {fc.HOLD_SECONDS // 60} minutes)")
        print(json.dumps(data, indent=1)[:1500])
        for key in ("reservation_id", "pending_reservation_id", "id",
                    "reservationId", "TTID", "ttid"):
            if isinstance(data, dict) and data.get(key):
                res_id = data[key]
                print(f"\n>>> reservation id ({key}) = {res_id}")
                break
        if not res_id:
            print("\n!! Hold succeeded but no reservation id found in the response.")
            print("   Copy the JSON above into the chat so the field name can be added.")
    except PermissionError as e:
        print(f"HOLD REFUSED (auth): {e}")
    except Exception as e:
        print(f"HOLD FAILED: {type(e).__name__}: {e}")
    finally:
        if res_id:
            print("\n--- releasing hold ---")
            try:
                url = f"{fc.BASE}/index.php/api/booking/pending_reservation/{res_id}"
                r = client.session.delete(url, timeout=15)
                ok = r.status_code in (200, 204)
                print(f"DELETE {url}\n   HTTP {r.status_code}  {r.text[:200]!r}")
                print("   RELEASED" if ok else "   !! release may not have worked")
                if not ok:
                    _manual_cleanup(res_id)
            except Exception as e:
                print(f"   !! RELEASE FAILED: {e}")
                _manual_cleanup(res_id)

    print("\nPaste this whole output into the chat.")
    return 0


def _manual_cleanup(res_id):
    print(f"\n   To clear it by hand: open "
          f"https://foreupsoftware.com/index.php/booking/{COURSE_ID}#/teetimes "
          f"and remove the pending reservation from your cart.")
    print(f"   Reservation id: {res_id}")
    print("   Otherwise it lapses on its own in a few minutes.")


if __name__ == "__main__":
    sys.exit(main())
