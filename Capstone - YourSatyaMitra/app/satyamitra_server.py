import sqlite3
from mcp.server.fastmcp import FastMCP

# 1. Initialize the Server
mcp = FastMCP("SatyaMitra Reputation Server")
DB_FILE = "satyamitra.db"

# --- DATABASE SETUP (Runs on Startup) ---
def initialize_db():
    """Creates the database and seeds it with initial data."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Create Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS domain_reputation (
            domain TEXT PRIMARY KEY,
            status TEXT,     -- 'TRUSTED', 'SATIRE', 'PROPAGANDA', 'UNVERIFIED'
            confidence INT   -- 0 to 100
        )
    """)
    
    # Check if empty, then seed
    cursor.execute("SELECT count(*) FROM domain_reputation")
    if cursor.fetchone()[0] == 0:
        print("🌱 Seeding database with initial knowledge...")
        seed_data = [
            ("theonion.com", "SATIRE", 100),
            ("babylonbee.com", "SATIRE", 100),
            ("bbc.com", "TRUSTED", 95),
            ("reuters.com", "TRUSTED", 98),
            ("infowars.com", "PROPAGANDA", 90),
            ("dailyhealthmiracle.com", "UNVERIFIED", 10) # Fictional example
        ]
        cursor.executemany("INSERT INTO domain_reputation VALUES (?,?,?)", seed_data)
        conn.commit()
        
    conn.close()

# Run initialization immediately
initialize_db()

# --- THE TOOL EXPOSED TO THE AGENT ---
@mcp.tool()
def check_domain_reputation(url: str) -> str:
    """
    Queries SatyaMitra's internal database to check if a domain 
    is a known offender (Satire, Propaganda, or Trusted).
    """
    # Simple domain extraction (e.g., "https://www.bbc.com/news" -> "bbc.com")
    clean_domain = url.split("//")[-1].split("/")[0].replace("www.", "")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT status, confidence FROM domain_reputation WHERE domain = ?", (clean_domain,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        status, confidence = result
        return f"INTERNAL RECORD FOUND: {clean_domain} is classified as {status} with {confidence}% confidence."
    else:
        return "NO RECORD: This domain is not in SatyaMitra's archives. Proceed with external verification."

# --- START SERVER ---
if __name__ == "__main__":
    print("🙏 SatyaMitra Server is running...")
    mcp.run()