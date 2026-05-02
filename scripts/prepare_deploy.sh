#!/bin/bash
# Pre-deployment script: Generate audit cache before deploying to Modal

set -e

echo "🔍 Checking for audit cache..."

CACHE_DIR="server/live_results/audit_cache"

if [ -d "$CACHE_DIR" ] && [ "$(ls -A $CACHE_DIR)" ]; then
    echo "✅ Audit cache found with $(ls -1 $CACHE_DIR | wc -l) files"
    echo ""
    echo "Cached models:"
    ls -1 $CACHE_DIR | sed 's/\.json$//' | sed 's/^/  - /'
    echo ""
else
    echo "⚠️  No audit cache found. Generating now..."
    echo ""
    echo "This will run audits for all demo models (takes ~5-10 minutes)"
    echo ""
    
    # Run video demo once to populate cache
    python scripts/video_demo.py
    
    echo ""
    echo "✅ Audit cache generated"
fi

echo ""
echo "📦 Ready to deploy to Modal!"
echo ""
echo "Next steps:"
echo "  1. modal deploy modal_deploy.py"
echo "  2. Copy the endpoint URLs"
echo "  3. Add MODAL_ENDPOINT to Streamlit Cloud secrets"
echo ""
