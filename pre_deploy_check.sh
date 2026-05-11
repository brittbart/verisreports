#!/bin/bash
echo "Running pre-deploy checks..."
cd ~/projects/veris
source venv/bin/activate
python3 -c "
import ast, sys
files = ['api.py', 'extract_claims.py', 'verdict_engine.py', 'scheduler.py']
for f in files:
    try:
        ast.parse(open(f).read())
        print(f'✓ {f}')
    except SyntaxError as e:
        print(f'✗ {f}: {e}')
        sys.exit(1)
print('All syntax checks passed')
"
