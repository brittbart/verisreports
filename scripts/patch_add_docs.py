#!/usr/bin/env python3
"""
Adds /docs (Swagger UI) and /openapi.yaml routes to api_public.py.
Run once from ~/projects/veris.
"""
import sys

path = 'api_public.py'
with open(path) as f:
    content = f.read()

ANCHOR = "log = logging.getLogger(__name__)"
if content.count(ANCHOR) != 1:
    print(f"ERROR: anchor found {content.count(ANCHOR)} times")
    sys.exit(1)

ROUTES = '''
# ---------------------------------------------------------------------------
# /openapi.yaml and /docs (Swagger UI)
# ---------------------------------------------------------------------------

@api_public.route('/openapi.yaml')
def openapi_spec():
    import os
    from flask import send_from_directory, current_app
    # Serve from static/ directory
    static_dir = os.path.join(current_app.root_path, 'static')
    return send_from_directory(static_dir, 'openapi.yaml',
                               mimetype='application/yaml')


@api_public.route('/docs')
def swagger_ui():
    from flask import Response
    html = """<!DOCTYPE html>
<html>
<head>
  <title>Verum Signal API Docs</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" type="text/css"
        href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" >
</head>
<body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"> </script>
<script>
  SwaggerUIBundle({
    url: "/openapi.yaml",
    dom_id: '#swagger-ui',
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: "BaseLayout",
    tryItOutEnabled: true,
    persistAuthorization: true,
  })
</script>
</body>
</html>"""
    return Response(html, mimetype='text/html')

'''

content = content.replace(ANCHOR, ANCHOR + '\n' + ROUTES)

with open(path, 'w') as f:
    f.write(content)

print("✓ /docs and /openapi.yaml routes added to api_public.py")
