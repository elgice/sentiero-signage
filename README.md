# Sentiero Digital Signage

Auto-generated daily MP4 for the 65-inch vertical store screen.

## Setup

### Step 1 - Create repo and upload these files
- sentiero_video_pipeline.py
- requirements.txt
- .github/workflows/generate_video.yml
- assets/sentiero-logo-wh.png

### Step 2 - Add Secrets
Settings > Secrets and variables > Actions > New repository secret

| Secret | Value |
|--------|-------|
| WC_CONSUMER_KEY | Your WooCommerce Consumer Key |
| WC_CONSUMER_SECRET | Your WooCommerce Consumer Secret |

### Step 3 - First run
Actions tab > Run workflow button > wait 5-10 minutes

## Download URL (for the screen/CMS)

https://github.com/USERNAME/sentiero-signage/releases/download/latest/sentiero_new_collection.mp4

Replace USERNAME with your GitHub username.

## Optional env vars

| Variable | Default | Description |
|----------|---------|-------------|
| WC_BASE_URL | https://sentieroclothing.gr | Store URL |
| WC_CATEGORY_SLUG | new-collection | Category slug |
| NUM_PRODUCTS | 50 | Products per video |
