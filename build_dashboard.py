#!/usr/bin/env python3
"""Build out/dashboard.html from the latest run's out/results.json.

    python3 run_demo.py && python3 build_dashboard.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "out", "results.json")) as f:
    data = json.load(f)
with open(os.path.join(HERE, "dashboard_template.html")) as f:
    template = f.read()

inner = template.replace("/*__DATA__*/null", json.dumps(data))

page = ("<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Invoice Control Tower</title>\n"
        "</head>\n<body>\n" + inner + "\n</body>\n</html>\n")

out = os.path.join(HERE, "out", "dashboard.html")
with open(out, "w") as f:
    f.write(page)
print(f"Wrote {out} ({len(page):,} bytes)")
