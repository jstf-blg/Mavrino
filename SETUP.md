# Affiliate Pipeline — Setup Guide
# ==================================
# From zero to fully automated in ~2 hours

## What you're setting up

A fully automated system that:
1. Discovers trending product keywords daily (Google Trends + Amazon)
2. Fetches real product data, prices, and customer reviews from Amazon
3. Generates unique content per post via Claude API
4. Renders to clean HTML with affiliate links
5. Commits and deploys automatically via GitHub → Cloudflare Pages

No browser needed after setup. Runs every day at 3am UTC.


## Prerequisites (one-time, ~2 hours total)

### 1. Install Python dependencies (local machine)
```
pip install -r requirements.txt
```

### 2. Register a .com domain
- Go to cloudflare.com → Registrar → Register Domain
- Choose a niche-relevant .com (~$10/year)
- Example: bestairfryers.com, kitchenappliance reviews.com

### 3. Create GitHub repository
- Create a NEW public repo at github.com (public = unlimited Actions minutes)
- Copy all files from this folder into the repo root
- Push: git init && git add . && git commit -m "init" && git push

### 4. Connect Cloudflare Pages to GitHub
- Cloudflare dashboard → Pages → Create a project
- Connect to GitHub → select your repo
- Build settings:
  - Build command: (leave BLANK — static HTML, no build needed)
  - Build output directory: output
- Save and deploy → your site goes live at yourproject.pages.dev
- Add your custom domain under Custom Domains

### 5. Amazon Associates US account
- Go to: affiliate-program.amazon.com
- Sign up with your real details (requires US bank account or international wire)
- Add your website URL (use your Cloudflare Pages URL initially)
- You need to make 3 qualifying sales within 180 days — launch the site FIRST
- Get your Associate Tag (format: yourname-20)
- Apply for PA-API access once approved (takes 1–7 days after first 3 sales)
  - PA-API: affiliate-program.amazon.com → Tools → Product Advertising API

### 6. Get your API keys

#### Claude API (required)
- console.anthropic.com → API Keys → Create Key
- Cost: ~$0.001 per post with Haiku model = ~$0.20/day at 200 posts

#### SerpApi (for Google Trends keywords)
- serpapi.com → Sign up → API Key
- Free tier: 100 searches/month (enough to start)
- $75/month for 5,000 searches (needed at scale)

#### Apify (for Amazon product scraping + reviews)
- apify.com → Sign up → Settings → Integrations → API Token
- Free tier available, pay-per-use after that
- Used for: bestseller lists, product data, review scraping

### 7. Add secrets to GitHub
Go to: GitHub repo → Settings → Secrets and variables → Actions → New secret

Add each of these:
```
ANTHROPIC_API_KEY     = your claude api key
AMAZON_ACCESS_KEY     = your pa-api access key (after getting PA-API access)
AMAZON_SECRET_KEY     = your pa-api secret key
AMAZON_ASSOCIATE_TAG  = yourtag-20
SERPAPI_KEY           = your serpapi key
APIFY_TOKEN           = your apify token
SITE_DOMAIN           = https://yourdomain.com
SITE_NAME             = Your Site Name
AUTHOR_NAME           = Your Name
AUTHOR_BIO            = Consumer product researcher with 5+ years experience.
GH_PAT                = github personal access token (Settings → Developer settings → PAT → Fine-grained → repo write)
```

### 8. Create required config files
```bash
mkdir -p config logs output
echo "[]" > config/keyword_queue.json
echo "[]" > config/keywords_done.json
echo "[]" > config/posts_log.json
```

### 9. Test locally before automating
```bash
# Copy .env.example to .env and fill in your keys
cp .env.example .env
# Edit .env with your actual values

# Test with 2 posts, no git push
POSTS_PER_DAY=2 python run_pipeline.py --dry-run

# Real test with 2 posts (uses API credits)
POSTS_PER_DAY=2 python run_pipeline.py
```

### 10. Push and let GitHub Actions take over
```bash
git add .
git commit -m "Add pipeline"
git push
```

Go to GitHub → Actions → You should see the workflow listed.
It runs automatically every day at 3am UTC.
You can also trigger it manually from the Actions tab.


## Publishing velocity ramp (important for Google)

DO NOT start at 200 posts/day on a new domain.

Recommended ramp:
- Days 1–30:   5 posts/day  → build 150 pages, establish crawl pattern
- Days 31–60:  20 posts/day → build 600 more pages
- Days 61–90:  50 posts/day → build 1,500 more pages  
- Days 91+:    200 posts/day → full velocity

To change the daily limit, update POSTS_PER_DAY in GitHub Secrets.


## Monitoring

### Check what's running
GitHub → Actions → daily_pipeline → latest run → logs

### Check what's live
Cloudflare dashboard → Pages → your project → deployments

### Check post count
config/posts_log.json — list of everything published

### Debug a failed run
GitHub → Actions → failed run → download artifacts → logs/


## Monthly costs at scale (200 posts/day)

| Service | Cost |
|---|---|
| Domain | $0.83/month |
| Cloudflare Pages | $5/month (Pro, for 5,000 builds) |
| Claude API (Haiku) | ~$6/month (200 posts × $0.001 × 30) |
| SerpApi | $75/month (keywords) |
| Apify | ~$20/month (product + review scraping) |
| **Total** | **~$107/month** |

Expected revenue at 200 posts/day after 6 months:
Conservative: $500–$2,000/month
Realistic with good niches: $2,000–$8,000/month


## Legal checklist (US market)

- [x] FTC affiliate disclosure on every page (built into templates)
- [x] Privacy policy page (auto-generated)
- [x] Affiliate disclosure page (auto-generated)
- [ ] W-8BEN or W-9 tax form filed with Amazon (one-time)
- [ ] Amazon Associates ToS compliance (no fake reviews, no cookie stuffing)
- [ ] State nexus considerations if earning above thresholds (consult accountant)
