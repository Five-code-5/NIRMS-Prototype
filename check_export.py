"""Finds out why 'Export list as PDF' fails:   python check_export.py
It runs the export on your real database and shows the exact problem in plain words."""
import os
import traceback

os.environ.setdefault("NIRMS_CLIENT_SECRET", "check")
os.environ.setdefault("NIRMS_ADMIN_SECRET", "check")
import core            # noqa: E402
import reports_lib     # noqa: E402

problems = 0
print("1. Checking reports_lib.py")
for name in ("register_pdf", "summary_pdf", "ticket_pdf", "client_pdf"):
    ok = hasattr(reports_lib, name)
    problems += not ok
    print("   ", "OK     " if ok else "MISSING", name)
if not hasattr(reports_lib, "register_pdf"):
    print("   -> reports_lib.py is still the OLD version. Replace the whole file with the new one.")

print("2. Checking admin_app.py")
try:
    import admin_app
except Exception:                                                    # noqa: BLE001
    print("   admin_app.py does not even start:")
    traceback.print_exc()
    raise SystemExit(1)
routes = {r.rule: r.endpoint for r in admin_app.app.url_map.iter_rules()}
for rule, expected in (("/", "dashboard"), ("/reports/export.pdf", "export_pdf"), ("/reports/export.csv", "export_csv")):
    got = routes.get(rule)
    ok = got == expected
    problems += not ok
    print("   ", "OK     " if ok else "PROBLEM", f"{rule}  ->  {got}  (should be {expected})")
    if rule == "/" and got != "dashboard":
        print("   -> Two old lines (@app.route(\"/\") and @admin_required) are left above another function. Delete them.")

print("3. Running the export on your real database")
try:
    with admin_app.app.test_request_context("/reports/export.pdf"):
        rows, filters = admin_app._filtered_reports(core.get_db())
        print(f"    found {len(rows)} tickets")
        data = reports_lib.register_pdf("check", rows).read()
    print("    OK  PDF created,", len(data), "bytes")
except Exception:                                                    # noqa: BLE001
    problems += 1
    print("    FAILED. Send me the last 5 lines below:\n")
    traceback.print_exc()
print("\nEverything is fine." if not problems else "\nFix the items marked above, then try the button again.")