# Archive Curator

Search, filter, and curate artifacts from archive.org into a browsable collection.

## Features

- Search archive.org by configurable categories (artists, musicians, filmmakers, etc.)
- Confidence scoring filters out academic papers, interviews, low-quality items
- Engagement filtering (minimum downloads/favorites)
- Fuzzy duplicate detection (98% title similarity)
- CSV-based data storage for easy editing
- Interactive HTML viewer with category/artist navigation
- Append mode to build up collection across multiple searches

## Requirements

- Python 3.9+
- archive.org account with S3 API keys (for adding to lists)

## Installation

```bash
cd archive-curator

# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

**Important:** Activate the virtual environment every time you open a new terminal:

```bash
source .venv/bin/activate
```

## Quick Start

```bash
source .venv/bin/activate

# Search a category and export to CSV
python main.py search -t literature -e csv -o output/data.csv

# Generate the HTML viewer
python main.py viewer --csv data.csv

# Start local server and open in browser
cd output && python -m http.server 8000
# Open http://localhost:8000/viewer.html
```

## Workflow

### 1. Search and Export to CSV

```bash
# Search a specific category
python main.py search -t visual_artists -e csv -o output/data.csv

# Search another category and APPEND to existing data
python main.py search -t literature -e csv -o output/data.csv --append

# Search with more results per term (default: 50)
python main.py search -t film -e csv -o output/data.csv --append --max-results 100
```

### 2. Generate HTML Viewer

```bash
python main.py viewer --csv data.csv
```

This creates `output/viewer.html` that reads from `data.csv`.

### 3. View Results (Local Server Required)

The HTML viewer uses JavaScript to load the CSV, which requires a web server due to browser security restrictions.

```bash
cd output
python -m http.server 8000
```

Then open http://localhost:8000/viewer.html in your browser.

### 4. Edit and Refresh

1. Edit `output/data.csv` in any spreadsheet app or text editor
2. Add, remove, or modify rows
3. Refresh the browser to see changes

### CSV Columns

| Column | Description |
|--------|-------------|
| category | Category name (e.g., "literature") |
| search_term | Artist/search term name |
| title | Item title |
| identifier | archive.org identifier |
| url | Full archive.org URL |
| mediatype | Type (texts, audio, movies, etc.) |
| confidence_score | Calculated quality score (0-100) |
| creator | Item creator/author |
| publisher | Publisher name |
| page_count | Number of pages (for texts) |

## Deploying to GitHub Pages

The viewer works on any static hosting since it's a real web server (unlike local `file://`).

### Deploy Command

```bash
# Copy files to deployed/ directory
python main.py deploy

# Or deploy and push in one command
python main.py deploy --commit --push
```

This copies:
- `output/viewer.html` → `deployed/index.html`
- `output/data.csv` → `deployed/data.csv`

### GitHub Pages Setup

1. Go to your repo **Settings → Pages**
2. Set source to **Deploy from a branch**
3. Set branch to **main** and folder to **/deployed**
4. Save and wait for deployment

### Full Workflow

```bash
# 1. Search and build your collection
python main.py search -t literature -e csv -o output/data.csv
python main.py search -t visual_artists -e csv -o output/data.csv --append

# 2. Generate the viewer
python main.py viewer

# 3. Deploy and push
python main.py deploy --commit --push
```

Your site will be live at `https://yourusername.github.io/your-repo/`

### Alternative: Standalone HTML

For a single self-contained file (no CSV needed):

```bash
python main.py search -t visual_artists -e html -o deployed/index.html
git add deployed && git commit -m "Deploy" && git push
```

## Configuration

### Search Terms (`config/categories.yaml`)

```yaml
visual_artists:
  description: "Visual artists and art books"
  mediatype: [texts]
  terms:
    - name: "Bruce Conner"
    - name: "Joseph Cornell"
    - name: "Yoko Ono"
      search_term: "Yoko Ono art"  # custom search query
      mediatype: [texts, audio]     # override category default

literature:
  description: "Authors and literary works"
  mediatype: [texts]
  terms:
    - name: "William S. Burroughs"
    - name: "Kathy Acker"
```

### Filters (`config/filters.yaml`)

```yaml
# Minimum score to include (0-100)
min_confidence: 60

# Engagement thresholds
min_downloads: 10
min_favorites: 1

# Patterns that reduce confidence
academic_patterns:
  - "thesis"
  - "dissertation"
  - "university of"

# Publishers that increase confidence
trusted_publishers:
  - "Taschen"
  - "MIT Press"
```

## Commands Reference

### search

```bash
python main.py search [OPTIONS]

Options:
  -t, --category TEXT      Specific category to search
  -m, --max-results INT    Max results per search term (default: 50)
  -e, --export FORMAT      Export format: csv, html, json
  -o, --output PATH        Output file path
  --append                 Append to existing CSV instead of overwriting
  -a, --show-all           Include items that failed confidence threshold
  -d, --details            Show detailed scoring for each item
  --no-metadata            Skip metadata fetch (faster but less accurate)
```

### viewer

```bash
python main.py viewer [OPTIONS]

Options:
  -c, --csv TEXT     CSV filename (default: data.csv)
  -o, --output PATH  Output HTML path (default: output/viewer.html)
  -t, --title TEXT   Page title
```

### categories

```bash
python main.py categories   # List all configured categories and terms
```

### deploy

```bash
python main.py deploy [OPTIONS]

Options:
  -c, --source-csv PATH    Source CSV file (default: output/data.csv)
  -h, --source-html PATH   Source HTML viewer (default: output/viewer.html)
  -d, --deploy-dir PATH    Deployment directory (default: deployed)
  --commit                 Git commit the changes
  --push                   Git push after commit (implies --commit)
```

### check-auth

```bash
python main.py check-auth   # Verify archive.org API credentials
```

## Confidence Scoring

Items start at 70 points. Adjustments:

| Factor | Points |
|--------|--------|
| Academic paper patterns | -40 |
| Interview content (audio) | -50 |
| Live recording (audio) | -30 |
| Under 50 pages (texts) | -25 |
| Over 200 pages (texts) | +10 |
| Trusted publisher | +15 |
| Trusted collection | +10 |
| Preferred format (FLAC, PDF) | +5 |
| Popular (1000+ downloads) | +1-10 |

Items scoring >= 60 pass by default.

## Troubleshooting

### "Failed to fetch" error in viewer

This happens when opening the HTML file directly (`file://` protocol). Browsers block local file access for security.

**Solution:** Run a local web server:

```bash
cd output
python -m http.server 8000
# Open http://localhost:8000/viewer.html
```

### Slow searches

Searches are parallelized but still depend on archive.org API response times. Tips:

- Use `--max-results 25` for faster iteration
- Use `--no-metadata` to skip detailed metadata fetching
- Search specific categories with `-t category_name`

### No results found

Check:
- `min_downloads` and `min_favorites` in `config/filters.yaml` (try setting to 0)
- Search term spelling in `config/categories.yaml`
- Run with `--show-all` to see items that failed filters
