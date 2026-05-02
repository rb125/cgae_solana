# Quick Deploy Commands

## 0. Pre-deployment: Generate Audit Cache
```bash
# Run this ONCE before deploying to populate audit cache
./scripts/prepare_deploy.sh
```

This runs the video demo locally to cache audit results for all models.

## 1. Install Modal
```bash
pip install modal
modal setup
```

## 2. Verify Your Secret
Your `azure_credentials` secret should contain:
- `AZURE_API_KEY`
- `AZURE_OPENAI_API_ENDPOINT`
- `DDFT_MODELS_ENDPOINT`
- `PRIVATE_KEY` (for Filecoin)
- `FILECOIN_PRIVATE_KEY`
- `CGAE_REGISTRY_ADDRESS`
- `CGAE_ESCROW_ADDRESS`

Check with:
```bash
modal secret list
```

## 3. Deploy Backend
```bash
modal deploy modal_deploy.py
```

Copy the endpoint URLs shown after deployment:
- `get_results` endpoint
- `list_results` endpoint

## 4. Deploy Dashboard

1. Push to GitHub
2. Go to https://share.streamlit.io
3. New app → Select repo → `dashboard/app.py`
4. Add secret in Streamlit settings:
   ```
   MODAL_ENDPOINT = "https://your-username--cgae-economy-get-results.modal.run"
   ```

## 5. Start Backend
```bash
modal run modal_deploy.py
```

Done! Dashboard will read from Modal backend using cached audits.

## Test Locally First
```bash
# Terminal 1: Run backend with cached audits
python -m server.live_runner --rounds 10

# Terminal 2: Run dashboard
streamlit run dashboard/app.py
```

## Updating Audit Cache

If you add new models or want to refresh audits:

```bash
# Run video demo to regenerate cache
python scripts/video_demo.py

# Redeploy to Modal
modal deploy modal_deploy.py
```
