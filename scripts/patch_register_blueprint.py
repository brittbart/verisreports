#!/usr/bin/env python3
"""
Registers the api_public blueprint in api.py.
Run once from ~/projects/veris.

What it does:
  1. Finds the line where existing blueprints are registered (outlet_routes, debate_routes, etc.)
  2. Adds the import and register_blueprint call for api_public.

Safe: validates anchor exists exactly once before writing.
"""

import sys

path = 'api.py'
with open(path) as f:
    content = f.read()

# Find where other blueprints are imported — use outlet_routes as anchor
# since that's a known Day 22-23 addition
IMPORT_ANCHOR = 'from outlet_routes import outlet_bp'
if content.count(IMPORT_ANCHOR) != 1:
    print(f"ERROR: import anchor found {content.count(IMPORT_ANCHOR)} times (expected 1)")
    sys.exit(1)

content = content.replace(
    IMPORT_ANCHOR,
    IMPORT_ANCHOR + '\nfrom api_public import api_public'
)

# Find where blueprints are registered
REGISTER_ANCHOR = 'app.register_blueprint(outlet_bp)'
if content.count(REGISTER_ANCHOR) != 1:
    print(f"ERROR: register anchor found {content.count(REGISTER_ANCHOR)} times (expected 1)")
    sys.exit(1)

content = content.replace(
    REGISTER_ANCHOR,
    REGISTER_ANCHOR + '\napp.register_blueprint(api_public)'
)

with open(path, 'w') as f:
    f.write(content)

print("✓ api_public blueprint registered in api.py")
print("  Added: from api_public import api_public")
print("  Added: app.register_blueprint(api_public)")
