import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(dbname=os.getenv('DB_NAME'),user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),host=os.getenv('DB_HOST'),port=os.getenv('DB_PORT'))
cur = conn.cursor()

mappings = {
    'foxnews.com': ['Fox News', 'Latest & Breaking News on Fox News', 'Latest Political News on Fox News'],
    'theguardian.com': ['US news | The Guardian', 'World news | The Guardian', '"site:theguardian.com" - Google News'],
    'bbc.co.uk': ['BBC News', '"site:bbc.co.uk" - Google News', 'bbci.co.uk', 'bbc.com'],
    'npr.org': ['NPR Topics: News', 'NPR Topics: Politics'],
    'nytimes.com': ['NYT > Top Stories', 'NYT > U.S. > Politics'],
    'breitbart.com': ['Breitbart News', '"site:breitbart.com" - Google News'],
    'newsmax.com': ['"site:newsmax.com" - Google News'],
    'thefederalist.com': ['"site:thefederalist.com" - Google News'],
}

for canonical, variants in mappings.items():
    placeholders = ','.join(['%s'] * len(variants))
    cur.execute(f'SELECT COALESCE(SUM(total_claims_checked),0), COALESCE(SUM(supported_count),0), COALESCE(SUM(disputed_count),0), COALESCE(SUM(overstated_count),0), COALESCE(SUM(plausible_count),0), COALESCE(SUM(not_verifiable_count),0), COALESCE(SUM(corroborated_count),0), COALESCE(SUM(not_supported_count),0), COALESCE(SUM(opinion_count),0) FROM sources WHERE name IN ({placeholders})', variants)
    sums = cur.fetchone()
    cur.execute('UPDATE sources SET total_claims_checked=total_claims_checked+%s, supported_count=supported_count+%s, disputed_count=disputed_count+%s, overstated_count=overstated_count+%s, plausible_count=plausible_count+%s, not_verifiable_count=not_verifiable_count+%s, corroborated_count=corroborated_count+%s, not_supported_count=not_supported_count+%s, opinion_count=opinion_count+%s WHERE name=%s', (*sums, canonical))
    cur.execute(f'DELETE FROM sources WHERE name IN ({placeholders})', variants)
    print(f'{canonical}: merged {cur.rowcount} variants, added {sums[0]} claims')

conn.commit()
conn.close()
print('Done.')
