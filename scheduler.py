import os
import sys
import time
import logging
from datetime import datetime
from dotenv import load_dotenv
import os
if os.path.exists('.env'):
    load_dotenv(override=False)

# Set up logging so you can see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

# How often to run each step (in hours)
FETCH_INTERVAL_HOURS = 3
VERDICT_INTERVAL_HOURS = 6
VERDICTS_PER_RUN = 20

def run_fetch():
    """Fetch new articles."""
    log.info("Starting article fetch...")
    try:
        import fetch_articles
        articles = fetch_articles.fetch_articles()
        log.info(f"Fetched {len(articles)} articles")
        return True
    except Exception as e:
        log.error(f"Fetch failed: {str(e)}")
        return False

def run_gdelt():
    """Fetch historical articles from GDELT."""
    log.info("Starting GDELT seed...")
    try:
        import gdelt_seed
        import json, os, psycopg2
        from dotenv import load_dotenv
        load_dotenv()
        articles = gdelt_seed.seed_gdelt(days_back=7)
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD'),
            host=os.getenv('DB_HOST')
        )
        cur = conn.cursor()
        added = 0
        for a in articles:
            try:
                cur.execute(
                    'INSERT INTO articles (title, source_name, url, published_at, description, content, processed) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (url) DO NOTHING RETURNING id',
                    (a.get('title',''), a.get('source',{}).get('name',''), a.get('url',''), None, a.get('description',''), a.get('content',''), False)
                )
                if cur.fetchone():
                    added += 1
            except:
                conn.rollback()
                continue
        conn.commit()
        conn.close()
        log.info(f"GDELT added {added} articles")
        return True
    except Exception as e:
        log.error(f"GDELT failed: {str(e)}")
        return False

def run_extract():
    """Extract claims from today's articles."""
    log.info("Starting claim extraction...")
    try:
        from datetime import datetime
        import extract_claims
        today = datetime.now().strftime('%Y-%m-%d')
        input_file = f"articles_{today}.json"
        
        if not os.path.exists(input_file):
            log.warning(f"No articles file found: {input_file}")
            return False
            
        extract_claims.process_articles(input_file, limit=50)
        log.info("Claim extraction complete")
        return True
    except Exception as e:
        import traceback
        log.error(f"Extraction failed: {str(e)}")
        log.error(traceback.format_exc())
        return False

def run_load():
    """Load articles and claims into database."""
    log.info("Loading to database...")
    try:
        from datetime import datetime
        import load_to_database
        today = datetime.now().strftime('%Y-%m-%d')
        articles_file = f"articles_{today}.json"
        claims_file = f"claims_{today}.json"
        
        if not os.path.exists(articles_file):
            log.warning("No articles file to load")
            return False
        if not os.path.exists(claims_file):
            log.warning("No claims file to load")
            return False
            
        load_to_database.load_articles(
            articles_file, claims_file
        )
        log.info("Database load complete")
        return True
    except Exception as e:
        log.error(f"Load failed: {str(e)}")
        return False

def run_priority():
    """Score claim priorities."""
    log.info("Scoring priorities...")
    try:
        import priority_scorer
        priority_scorer.score_all_claims()
        log.info("Priority scoring complete")
        return True
    except Exception as e:
        log.error(f"Priority scoring failed: {str(e)}")
        return False

def run_verdicts():
    """Submit batch of claims for verification (50% cost saving)."""
    log.info(f"Submitting batch verdict job ({VERDICTS_PER_RUN} claims)...")
    try:
        from verdict_engine import run_batch_verdict_engine, process_batch_results
        # First process any pending batch results
        import os
        if os.path.exists("pending_batch.txt"):
            log.info("Processing pending batch results...")
            process_batch_results()
        # Submit new batch
        batch_id = run_batch_verdict_engine(limit=VERDICTS_PER_RUN)
        if batch_id:
            log.info(f"Batch submitted: {batch_id}")
        else:
            log.info("No claims to batch or all resolved via cache/consensus")
        return True
    except Exception as e:
        log.error(f"Verdict batch failed: {str(e)}")
        return False
    
    
    


def run_pre_verify():
    """Pre-verify claims for top outlet articles."""
    log.info("Pre-verifying top outlet articles...")
    try:
        import pre_verify
        count = pre_verify.pre_verify_articles(limit=30)
        log.info(f"Pre-verification complete: {count} claims verified")
        return True
    except Exception as e:
        log.error(f"Pre-verification failed: {str(e)}")
        return False

def run_full_pipeline():
    """Run the complete Veris pipeline."""
    start = datetime.now()
    log.info("="*50)
    log.info("VERIS PIPELINE STARTING")
    log.info(f"Time: {start.strftime('%Y-%m-%d %H:%M')}")
    log.info("="*50)
    
    steps = [
    ("Fetch articles", run_fetch),
    ("GDELT seed", run_gdelt),
    ("Extract claims", run_extract),
    ("Load to database", run_load),
    ("Pre-verify top outlets", run_pre_verify),
    ("Score priorities", run_priority),
    ("Assign verdicts", run_verdicts),]
    
    results = []
    for step_name, step_func in steps:
        log.info(f"\n--- {step_name} ---")
        success = step_func()
        results.append((step_name, success))
        
        if not success:
            log.warning(f"{step_name} had issues — continuing")
        
        # Small pause between steps
        time.sleep(2)
    
    end = datetime.now()
    duration = (end - start).seconds
    
    log.info("\n" + "="*50)
    log.info("PIPELINE COMPLETE")
    log.info(f"Duration: {duration} seconds")
    log.info("\nStep results:")
    for step, success in results:
        status = "✓" if success else "✗"
        log.info(f"  {status} {step}")
    log.info("="*50)

def start_scheduler():
    """Run the pipeline on a schedule."""
    
    log.info("Veris scheduler starting...")
    log.info(f"Fetch interval: every {FETCH_INTERVAL_HOURS} hours")
    log.info(f"Verdict interval: every {VERDICT_INTERVAL_HOURS} hours")
    log.info("Running first pipeline now...\n")
    
    last_fetch = 0
    last_verdict = 0
    
    while True:
        now = time.time()
        
        # Check if it's time to fetch
        hours_since_fetch = (now - last_fetch) / 3600
        
        if hours_since_fetch >= FETCH_INTERVAL_HOURS:
            run_full_pipeline()
            last_fetch = time.time()
            last_verdict = time.time()
        
        # Check if it's time for extra verdict runs
        hours_since_verdict = (now - last_verdict) / 3600
        
        if hours_since_verdict >= VERDICT_INTERVAL_HOURS:
            log.info("Running extra verdict batch...")
            run_verdicts()
            last_verdict = time.time()
        
        # Check every 15 minutes
        next_check = 15 * 60
        log.info(f"Next check in 15 minutes...")
        time.sleep(next_check)

if __name__ == "__main__":
    # Check for command line arguments
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        # Run pipeline once and exit
        run_full_pipeline()
    else:
        # Run continuously on schedule
        start_scheduler()