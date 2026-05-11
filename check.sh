#!/bin/bash
echo "=== Syntax check ==="
python3 -c "import ast; ast.parse(open('api.py').read()); print('api.py OK')"

echo "=== Template variable audit ==="
# Find all {{var}} in template
vars=$(grep -o '{{[^}]*}}' templates/report.html | sort -u)
echo "Template expects:"
echo "$vars"

echo ""
echo "=== Variables set in api.py ==="
grep "html.replace" api.py | grep -o "'{{[^}]*}}'" | sort -u

echo ""
echo "=== MISSING (in template but not replaced) ==="
for var in $vars; do
    clean=$(echo "$var" | sed 's/[{}]//g' | xargs)
    if ! grep -q "$clean" api.py; then
        echo "  MISSING: $var"
    fi
done
