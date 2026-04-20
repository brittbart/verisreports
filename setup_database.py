import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def create_tables():
    """Create all the tables Veris needs."""
    
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST')
    )
    
    cursor = conn.cursor()
    
    print("Creating tables...")
    
    # Articles table — stores every article ingested
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            source_name TEXT,
            url TEXT UNIQUE,
            published_at TIMESTAMP,
            description TEXT,
            content TEXT,
            fetched_at TIMESTAMP DEFAULT NOW(),
            processed BOOLEAN DEFAULT FALSE
        );
    """)
    print("  ✓ articles table")
    
    # Sources table — stores outlet profiles
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            url TEXT,
            editorial_lean TEXT,
            total_claims_checked INTEGER DEFAULT 0,
            verified_count INTEGER DEFAULT 0,
            disputed_count INTEGER DEFAULT 0,
            overstated_count INTEGER DEFAULT 0,
            not_verifiable_count INTEGER DEFAULT 0,
            reliability_score TEXT,
            first_analysed TIMESTAMP,
            last_analysed TIMESTAMP DEFAULT NOW()
        );
    """)
    print("  ✓ sources table")
    
    # Claims table — stores every extracted claim
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS claims (
            id SERIAL PRIMARY KEY,
            article_id INTEGER REFERENCES articles(id),
            claim_text TEXT NOT NULL,
            speaker TEXT,
            claim_type TEXT,
            why_checkworthy TEXT,
            verdict TEXT,
            confidence_score INTEGER,
            verdict_summary TEXT,
            full_analysis TEXT,
            sources_used TEXT,
            times_seen INTEGER DEFAULT 1,
            first_seen TIMESTAMP DEFAULT NOW(),
            last_seen TIMESTAMP DEFAULT NOW(),
            last_checked TIMESTAMP
        );
    """)
    print("  ✓ claims table")
    
    # Topic clusters table — groups claims by subject
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topic_clusters (
            id SERIAL PRIMARY KEY,
            topic_name TEXT UNIQUE NOT NULL,
            description TEXT,
            claim_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    print("  ✓ topic_clusters table")
    
    # Claim topics junction table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS claim_topics (
            claim_id INTEGER REFERENCES claims(id),
            topic_id INTEGER REFERENCES topic_clusters(id),
            PRIMARY KEY (claim_id, topic_id)
        );
    """)
    print("  ✓ claim_topics table")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    print("\n✓ Database setup complete")
    print("✓ All tables created successfully")

if __name__ == "__main__":
    create_tables()