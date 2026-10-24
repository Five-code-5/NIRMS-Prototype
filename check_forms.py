"""Shows which forms are missing the security token:   python check_forms.py"""
import glob
import re

bad = 0
for path in sorted(glob.glob("*_templates/*.html")):
    html = open(path, encoding="utf-8").read()
    for m in re.finditer(r'<form[^>]*method="post"[^>]*>(.*?)</form>', html, re.S | re.I):
        ok = "csrf_token" in m.group(1)
        bad += not ok
        print("OK      " if ok else "MISSING ", path)
print("\nAll forms are protected." if not bad else f"\n{bad} form(s) need the csrf_token line.")