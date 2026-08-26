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
    ap.add_argument("--handoff", action="store_true",
                    help="test whether a hold made by THIS session blocks a "
                         "different session (i.e. your phone)")
    ap.add_argument("--hold-open", action="store_true",
                    help="hold a slot and KEEP it while you check your phone, "
                         "then release on Enter. Use with --login.")
    args = ap.parse_args()

    if args.handoff:
        return handoff_test(args)
    if args.hold_open:
        return hold_open_test(args)

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


def hold_open_test(args) -> int:
    """
    Hold a slot and keep it alive while you go look at ForeUp on another device.

    This is the test the automated handoff probe cannot do: whether a hold placed
    by the server, WHILE LOGGED IN AS YOU, becomes visible or completable in your
    own browser once you log in there. The course has
    `multiple-user-accounts-and-pending-reservations` enabled, which is the only
    reason to expect it might.

    Releases on Enter, and refreshes in the background so it does not lapse while
    you look.
    """
    import threading

    if args.login:
        email = os.environ.get("FOREUP_EMAIL", "")
        password = os.environ.get("FOREUP_PASSWORD", "")
        if not email or not password:
            print("--hold-open --login needs FOREUP_EMAIL / FOREUP_PASSWORD set.")
            return 2
        client = fc.ForeUpClient(email, password)
        print(f"Logging in as {email} …")
        client.login(COURSE_ID)
        print("   logged in OK — the hold will belong to THIS account\n")
    else:
        client = fc.ForeUpClient()
        print("Holding ANONYMOUSLY — you almost certainly want --login here,\n"
              "since an anonymous hold has no account to hand off to.\n")

    times = client.fetch_tee_times(
        course_id=COURSE_ID, schedule_id=SCHEDULE_ID, date=args.date,
        time_from="00:00", time_to="23:59", players=args.players,
        booking_class=BOOKING_CLASS)
    if not times:
        print(f"No open times on {args.date}.")
        return 1
    slot = times[-1]
    print(f"Slot: {slot['time']}   ${slot.get('green_fee')}   (LAST slot of the day)")

    input("\nPress Enter to hold it (Ctrl+C to abort) … ")

    res_id = None
    stop = threading.Event()
    try:
        raw = client.hold(slot, players=args.players, holes=18)
        res_id = raw.get("reservation_id")
        print(f"\nHELD — reservation {res_id}")

        def keep_warm():
            while not stop.wait(60):
                ok = client.refresh_hold(res_id)
                print(f"   [keep-alive] refresh {'ok' if ok else 'REJECTED'}")
        threading.Thread(target=keep_warm, daemon=True).start()

        print(f"""
NOW GO CHECK, while this stays held:

  1. Open https://foreupsoftware.com/index.php/booking/{COURSE_ID}#/teetimes
     on your phone or another browser.
  2. Log in with the SAME ForeUp account.
  3. Look for {slot['time']} on {args.date}.

What do you see?
  (a) the slot is gone / "no longer available"   -> handoff does NOT work
  (b) a countdown or the time sitting in your cart -> handoff DOES work
""")
        input("Press Enter when you're done looking, to release the hold … ")
    except Exception as e:
        print(f"HOLD FAILED: {type(e).__name__}: {e}")
    finally:
        stop.set()
        if res_id:
            ok = client.release_hold(res_id)
            print(f"\nReleased {res_id}: {'ok' if ok else 'FAILED'}")
            if not ok:
                _manual_cleanup(res_id)

    print("\nPaste this output plus what you saw into the chat.")
    return 0


def _slot_view(client, date, players, want_time):
    """What one session sees for a given tee time: spots, or gone entirely."""
    times = client.fetch_tee_times(
        course_id=COURSE_ID, schedule_id=SCHEDULE_ID, date=date,
        time_from="00:00", time_to="23:59", players=players,
        booking_class=BOOKING_CLASS)
    for t in times:
        if t.get("time") == want_time:
            return t.get("available_spots")
    return None      # not listed at all


def handoff_test(args) -> int:
    """
    The question that decides whether snipe mode is useful at all:

    the hold is created by the SERVER's ForeUp session. Your phone is a
    different session. If ForeUp scopes a pending reservation to the session
    that made it, then the server holding a slot makes it look taken to YOU —
    the app would be blocking its own user, which is exactly the "in someone
    else's cart" spinner we are trying to avoid.

    Session A holds the last slot of the day. Session B — a completely fresh
    client, standing in for your phone — then looks at the same tee sheet and
    tries to hold the same slot. Both holds are released at the end.
    """
    a = fc.ForeUpClient()                     # "the server"
    b = fc.ForeUpClient()                     # "your phone"

    times = a.fetch_tee_times(
        course_id=COURSE_ID, schedule_id=SCHEDULE_ID, date=args.date,
        time_from="00:00", time_to="23:59", players=args.players,
        booking_class=BOOKING_CLASS)
    if not times:
        print(f"No open times on {args.date}.")
        return 1
    slot = times[-1]
    when = slot["time"]

    print(f"Slot under test : {when}   ${slot.get('green_fee')}")
    print(f"Spots before    : A sees {_slot_view(a, args.date, args.players, when)}, "
          f"B sees {_slot_view(b, args.date, args.players, when)}")

    input("\nPress Enter to run the handoff test (Ctrl+C to abort) … ")

    a_id = b_id = None
    try:
        print("\n[A] placing hold …")
        raw = a.hold(slot, players=args.players, holes=18)
        a_id = raw.get("reservation_id")
        print(f"[A] held, reservation {a_id}")

        print(f"\n[A] sees {_slot_view(a, args.date, args.players, when)} spots after its own hold")
        b_spots = _slot_view(b, args.date, args.players, when)
        print(f"[B] sees {b_spots} spots  "
              f"({'SLOT GONE' if b_spots is None else 'still listed'})")

        print("\n[B] trying to hold the same slot (this is your phone clicking it) …")
        try:
            raw_b = b.hold(slot, players=args.players, holes=18)
            b_id = raw_b.get("reservation_id")
            print(f"[B] HOLD SUCCEEDED — reservation {b_id}")
            print("    => holds do NOT block other sessions; the slot is still")
            print("       grabbable by anyone, so holding does not win the race.")
        except Exception as e:
            print(f"[B] hold refused: {type(e).__name__}: {str(e)[:200]}")
            print("    => the hold DOES lock the slot to session A.")
            print("       Good news for the race, bad news for handoff: your phone")
            print("       is session B and would see exactly this.")

        print("\n[A] testing refresh (course has prevent-indefinite-pending-reservations ON) …")
        for i in range(1, 4):
            ok = a.refresh_hold(a_id) if a_id else False
            print(f"    refresh {i}: {'ok' if ok else 'REJECTED'}")
            if not ok:
                break
            time.sleep(2)
    except Exception as e:
        print(f"\nTEST ERROR: {type(e).__name__}: {e}")
    finally:
        for label, client, rid in (("A", a, a_id), ("B", b, b_id)):
            if rid:
                try:
                    ok = client.release_hold(rid)
                    print(f"\n[{label}] released {rid}: {'ok' if ok else 'FAILED'}")
                    if not ok:
                        _manual_cleanup(rid)
                except Exception as e:
                    print(f"[{label}] release failed: {e}")
                    _manual_cleanup(rid)

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
