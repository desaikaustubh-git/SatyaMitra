import operator
import os
import sqlite3
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from typing import Annotated, TypedDict, List, Literal, Optional
from langgraph.graph import StateGraph, START, END
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from dotenv import load_dotenv

# --- 1. SETUP ---
DIAGRAM_PATH = "satyamitra_workflow.png"

# Load environment variables from a .env file
load_dotenv()

# Check if key exists
if "GOOGLE_API_KEY" not in os.environ:
    raise ValueError("GOOGLE_API_KEY not found. Please create a .env file and add your key.")

# Use Gemini 2.5 Flash for speed and multimodal capabilities
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.3)
search_tool = DuckDuckGoSearchRun()
DB_FILE = "satyamitra.db"

# --- 2. DEFINE STATE ---
class SatyaMitraState(TypedDict):
    messages: Annotated[List[dict], operator.add]
    revision_count: int
    is_verified: bool
    verdict: str
    input_type: str
    image_data: Optional[str] 
    domain_status: Optional[str] 
    claim_text: str 
    user_role: str

# --- 3. HELPER TOOLS ---

def check_internal_db(url: str) -> Optional[str]:
    """Checks the local SQLite database for known domain reputation (MCP Tool)."""
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS domain_reputation (domain TEXT PRIMARY KEY, status TEXT, confidence INT)")
        cursor.execute("SELECT status, confidence FROM domain_reputation WHERE domain = ?", (domain,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return f"INTERNAL RECORD FOUND: {domain} is confirmed as **{result[0]}** (Confidence: {result[1]}%)."
        return None
    except Exception as e:
        print(f"DB Check Error: {e}")
        return None

def update_internal_db(url: str, verdict: str, original_claim: str):
    """
    Learns from a new verification and updates the database status for the domain.
    """
    if not url or not verdict:
        return
    
    try:
        domain = urlparse(url).netloc.replace("www.", "")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        status_map = {
            "TRUE": ("TRUSTED", 90), 
            "FALSE": ("PROPAGANDA", 95), 
            "MISLEADING": ("UNVERIFIED", 70), 
            "UNVERIFIED": ("UNVERIFIED", 50)
        }
        
        status, confidence = status_map.get(verdict.upper(), ("UNVERIFIED", 50))
        
        cursor.execute("""
            INSERT OR REPLACE INTO domain_reputation (domain, status, confidence)
            VALUES (?, ?, ?)
        """, (domain, status, confidence))
        
        conn.commit()
        conn.close()
        print(f"✅ DB Updated: {domain} marked as {status}.")
    except Exception as e:
        print(f"❌ DB Update Error: {e}")

def scrape_website(url: str) -> dict:
    """Scrapes text content and image URLs from a given URL."""
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        title = soup.title.string if soup.title else "No Title"
        
        paragraphs = [p.get_text() for p in soup.find_all('p')]
        text_content = " ".join(paragraphs)
        
        image_urls = []
        for img in soup.find_all('img'):
            src = img.get('src')
            if src:
                absolute_url = urljoin(url, src)
                if not absolute_url.endswith(('.svg', '.ico')) and 'icon' not in absolute_url.lower():
                     image_urls.append(absolute_url)
        
        seen = set()
        unique_image_urls = [x for x in image_urls if not (x in seen or seen.add(x))]

        return {
            "title": title,
            "text": text_content[:5000], 
            "images": unique_image_urls[:3] 
        }
    except Exception as e:
        return {"error": f"Error scraping URL: {str(e)}"}
        
# --- NEW FUNCTION: Source Logging (Requires new table) ---
def log_source(claim_id: int, source_type: str, source_identifier: str, verdict: str):
    """Logs the individual sources used during research."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id INTEGER,
                source_type TEXT,
                source_identifier TEXT,
                verdict TEXT,
                FOREIGN KEY(claim_id) REFERENCES verification_history(id)
            )
        """)
        cursor.execute("""
            INSERT INTO source_logs (claim_id, source_type, source_identifier, verdict)
            VALUES (?, ?, ?, ?)
        """, (claim_id, source_type, source_identifier, verdict))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Source Log Error: {e}")

# --- 4. DEFINE NEW NODES (Specialized Agents) ---

async def start_node(state: SatyaMitraState):
    """Formal entry point that passes the initial state."""
    return {"messages": state["messages"]}

async def pre_processor_node(state: SatyaMitraState):
    """Entry node: Determines input type and extracts core claim (Context Engineering)."""
    
    last_msg = state["messages"][-1]
    if isinstance(last_msg, tuple): input_text = last_msg[1]
    elif hasattr(last_msg, "content"): input_text = last_msg.content
    else: input_text = str(last_msg)

    input_type = state.get("input_type", "text")
    
    # 2. Context Compaction: Extract the verifiable claim
    if input_type == "url":
        scraped_data = scrape_website(input_text)
        if "error" in scraped_data:
            claim = input_text
        else:
            extraction_prompt = f"Extract the single, most verifiable claim from this text: {scraped_data['text'][:2000]}"
            extraction = await llm.ainvoke([HumanMessage(content=extraction_prompt)])
            claim = extraction.content
        
    elif input_type == "image":
        claim = "Analyze image for authenticity and context."
    else:
        claim = input_text
        
    return {
        "messages": state["messages"], 
        "claim_text": claim,
        "domain_status": None, 
        "image_data": state.get("image_data", None),
        "user_role": state.get("user_role", 'standard')
    }

async def db_analyst_node(state: SatyaMitraState):
    """Specialized Agent: Checks internal Memory Bank (MCP)."""
    input_text = state["messages"][0].content if hasattr(state["messages"][0], 'content') else state["messages"][0][1]
    
    if state.get("input_type") == "url":
        db_record = check_internal_db(input_text)
        if db_record:
            print("✅ DB Analyst found record.")
            return {"domain_status": db_record}
        
    return {"domain_status": None} 

# --- 5. DEFINE CORE AGENTS (Updated) ---

async def researcher_node(state: SatyaMitraState):
    """Performs live search and multimodal analysis based on the input type."""
    print("--- 🕵️ Researcher Agent is executing deep search ---")
    
    claim = state["claim_text"]
    input_type = state.get("input_type", "text")
    uploaded_image_data = state.get("image_data", None)
    
    research_summary = ""

    if input_type == "image" and uploaded_image_data:
        # Multimodal Analysis (The image analysis logic)
        vision_prompt = """
        Role: You are an expert image analyzer who can easily understand the contents of the image and can spot fake images easily. 
        Your task: Analyze this image carefully. 
        1. Describe what is happening in the image. 
        2. Check for impossible events, visual anomalies (warped text, distorted faces/hands, artifacts suggesting editing/AI generation).
        """
        
        vision_message = HumanMessage(
            content=[
                {"type": "text", "text": vision_prompt},
                {"type": "image_url", "image_url": {"url": uploaded_image_data}}
            ]
        )
        
        analysis = await llm.ainvoke([vision_message])
        image_description = analysis.content
        
        search_query = f"fact check image: {image_description[:150]}"
        search_results = search_tool.invoke(search_query)
        research_summary = f"**Visual Analysis:**\n{image_description}\n\n**External Verification:**\n{search_results}"

    elif input_type == "url":
        # URL Handler logic (using extracted claim)
        search_results = search_tool.invoke(f"fact check {claim[:200]}")
        research_summary = f"**Claims:** {claim}\n**Verification:** {search_results}"
        
    else: # Text Handler
        search_results = search_tool.invoke(f"fact check {claim}")
        research_summary = search_results

    # Final summarization step for the Skeptic
    final_prompt = f"""
    Intelligence on claim '{claim}': {research_summary}. 
    Summarize findings for the Skeptic, focusing on the credibility of the sources and the visual consistency.
    """
    response = await llm.ainvoke([HumanMessage(content=final_prompt)])
    
    return {"messages": [response]}

async def skeptic_node(state: SatyaMitraState):
    """The Loop Agent: Critiques research quality and logs the decision."""
    print("--- ⚖️ Skeptic Agent is evaluating ---")
    last_research = state["messages"][-1].content
    retries = state["revision_count"]
    
    # Check if DB Analyst already flagged it, auto-approve the verdict
    if state.get("domain_status") and "INTERNAL RECORD FOUND" in state["domain_status"]:
        status_message = f"Internal Match ({state['domain_status']}). Auto-Approved."
        return {
            "messages": [AIMessage(content=status_message)], 
            "is_verified": True, 
            "revision_count": retries
        }

    prompt = f"Review this research: '{last_research}'. Is this sufficient? If vague/contradictory, say 'REJECTED [Reason]'. If solid, say 'APPROVED'."
    decision = await llm.ainvoke([HumanMessage(content=prompt)])
    decision_text = decision.content.strip()
    
    # Log the Skeptic's verdict explicitly (for the trace)
    print(f"Skeptic Verdict: {decision_text}")
    
    if decision_text.startswith("REJECTED") and retries < 1:
        return {
            "messages": [AIMessage(content=f"Critique: {decision_text}")], 
            "is_verified": False, 
            "revision_count": retries + 1
        }
    
    return {
        "messages": [AIMessage(content="Evidence accepted. Proceeding to final report.")], 
        "is_verified": True, 
        "revision_count": retries
    }

async def reporter_node(state: SatyaMitraState):
    """
    The Final Agent: Compiles the report, extracts the verdict, and updates the DB (RBAC check).
    """
    print("--- 📝 Reporter Agent is drafting a report ---")
    research = state["messages"][-2].content
    original_claim = state["claim_text"]
    original_input = state["messages"][0].content if hasattr(state["messages"][0], 'content') else state["messages"][0][1]
    
    user_role = state.get("user_role", 'standard')
    
    # 1. Generate Report with specific format instructions
    prompt = f"""
    Based on the following comprehensive research: 
    "{research}"
    
    You are an expert Investigator. Generate a response with two distinct parts for claim: "{original_claim}".
    
    PART 1: THE UI SUMMARY (Keep concise)
    Format exactly like this (ensure double newlines between sections):
    
    **Image Description:** [Brief description if image was analyzed, otherwise "N/A"]
    
    **Verdict:** [TRUE / FALSE / MISLEADING / UNVERIFIED]
    
    **Claim Analyzed:** {original_claim}
    
    **Summary:** [2-3 sentences explaining the verdict]
    
    PART 2: THE DETAILED REPORT (Professional and detailed)
    Start this section with the delimiter: "---DETAILED_REPORT_START---"
    Include: **Investigation Report**, **Claim Analyzed**, **Evidence Breakdown**, **Visual Analysis**, **Final Conclusion**.
    """
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    final_report_content = response.content
    
    # 2. Extract the Verdict for DB Update - FINAL, ROBUST STRING SPLITTING
    verdict_text = "UNVERIFIED" # Default if parsing fails
    
    # Split the report to isolate the summary part
    parts = final_report_content.split("---DETAILED_REPORT_START---")
    ui_summary_part = parts[0].strip()
    
    try:
        if '**Verdict:**' in ui_summary_part:
            # Split on the unique marker
            verdict_line = ui_summary_part.split('**Verdict:**')[1].strip()
            
            # Get the first word (e.g., FALSE, TRUE, MISLEADING).
            verdict_word = verdict_line.split()[0].replace('**', '').upper()
            
            # Check if the word is a valid verdict type
            if verdict_word in ["TRUE", "FALSE", "MISLEADING", "UNVERIFIED"]:
                verdict_text = verdict_word
            else:
                print(f"⚠️ Parsed verdict '{verdict_word}' is invalid. Defaulting to UNVERIFIED.")
    except Exception as e:
        print(f"❌ Critical Verdict Parsing Error: {e}. Defaulting to UNVERIFIED.")
        verdict_text = "UNVERIFIED" 


    # 3. DB UPDATE (RBAC CHECK & Persistence)
    if state.get("input_type") == "url" and user_role == 'admin':
        if original_input and original_input.startswith(('http://', 'https://')):
            update_internal_db(original_input, verdict_text, original_claim)
            print(f"🔒 DB WRITE SUCCESS: User {user_role} updated record for {original_input}.")
        else:
             print(f"⚠️ DB WRITE BLOCKED: Input was not a URL or was invalid.")
             
    elif state.get("input_type") == "url" and user_role != 'admin':
        print(f"⚠️ DB WRITE BLOCKED: User {user_role} does not have admin privileges.")
    
    # Log the attempt in verification history (for analytics, regardless of role/input type)
    if final_report_content:
        # Ensure the variables are definite strings before insertion
        claim_to_insert = str(original_claim)
        verdict_to_insert = str(verdict_text)

        # --- MOCK ORIGIN DATA: Replaces real Geo-IP lookup ---
        MOCK_ORIGINS = [("Mumbai", "India"), ("New York", "USA"), ("London", "UK"), ("Bengaluru", "India")]
        import random
        origin_city, origin_country = random.choice(MOCK_ORIGINS)
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # Insert main record (MUST happen first to get the ID)
        cursor.execute("""
            INSERT INTO verification_history (user_id, claim_text, verdict, origin_city, origin_country, user_role, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (state.get("user_id", "anonymous"), claim_to_insert, verdict_to_insert, origin_city, origin_country, user_role))
        
        claim_id = cursor.lastrowid # CRITICAL: Get the ID of the inserted record
        conn.commit()
        
        # Log Mock Sources using the new claim_id
        if claim_id:
            if state.get("input_type") == 'url' or state.get("input_type") == 'text':
                log_source(claim_id, "External Web", "DuckDuckGo/Search Engine", verdict_to_insert)
            if state.get("input_type") == 'url':
                 # Use the actual scraped domain as a mock source identifier
                 source_domain = urlparse(original_input).netloc.replace("www.", "") or "N/A"
                 log_source(claim_id, "Website Scraping", source_domain, verdict_to_insert)
            if state.get("input_type") == 'image':
                 log_source(claim_id, "AI Vision Model", "Gemini 2.5 Flash Vision", verdict_to_insert)
            
            # Log the internal DB check as a source
            log_source(claim_id, "Internal DB", "satyamitra.db (MCP)", verdict_to_insert)

        # Debug check for post-insert
        print("\n--- 💾 Post-Insert DB Check ---")
        cursor.execute("SELECT * FROM verification_history ORDER BY id DESC LIMIT 1")
        rows = cursor.fetchall()
        if rows:
             print(f"Last Inserted Row (Full Data): {rows[0]}")
        print("---------------------------------\n")

        conn.close()


    return {"messages": [response]}

# --- 6. GRAPH DEFINITION AND GENERATION ---

# Router to decide next step based on input type
def router_to_db(state: SatyaMitraState) -> Literal["db_analyst", "researcher"]:
    if state.get("input_type") == "url":
        return "db_analyst"
    else:
        return "researcher"

# Router after DB check
def router_after_db(state: SatyaMitraState) -> Literal["reporter", "researcher"]:
    if state.get("domain_status") and "INTERNAL RECORD FOUND" in state["domain_status"]:
        return "reporter"
    else:
        return "researcher"


# --- BUILDER ---
builder = StateGraph(SatyaMitraState)

builder.add_node("start", start_node)
builder.add_node("pre_processor", pre_processor_node)
builder.add_node("db_analyst", db_analyst_node) 
builder.add_node("researcher", researcher_node)
builder.add_node("skeptic", skeptic_node)
builder.add_node("reporter", reporter_node)

# 6b. Define Entry Point (Start of the Graph)
builder.set_entry_point("start")

builder.add_edge("start", "pre_processor") 

builder.add_conditional_edges(
    "pre_processor",
    router_to_db,
    {"db_analyst": "db_analyst", "researcher": "researcher"}
)

builder.add_conditional_edges(
    "db_analyst",
    router_after_db,
    {"reporter": "reporter", "researcher": "researcher"}
)

builder.add_edge("researcher", "skeptic")

builder.add_conditional_edges(
    "skeptic", 
    lambda state: "reporter" if state["is_verified"] else "researcher",
    {"reporter": "reporter", "researcher": "researcher"}
)

builder.add_edge("reporter", END)

satyamitra_brain = builder.compile()

# -------------------------------------------------------------
# FINAL CODE BLOCK FOR IMAGE GENERATION (Called by server.py)
# -------------------------------------------------------------

def generate_workflow_image(graph_object, filename=DIAGRAM_PATH):
    """
    Generates and saves the graph image to a PNG file using Graphviz.
    """
    try:
        from graphviz import Digraph
        
        # Define the graph colors and styles (matching frontend aesthetic)
        styles = {
            'start': {'shape': 'box', 'fillcolor': '#D8BFD8', 'color': '#4B0082'},
            'pre_processor': {'shape': 'circle', 'fillcolor': '#E6E6FA', 'color': '#4B0082'},
            'db_analyst': {'shape': 'circle', 'fillcolor': '#D8BFD8', 'color': '#4B0082'},
            'researcher': {'shape': 'circle', 'fillcolor': '#F0F8FF', 'color': '#0077B6'},
            'skeptic': {'shape': 'circle', 'fillcolor': '#FFDAB9', 'color': '#FF4B4B', 'penwidth': '3'},
            'reporter': {'shape': 'circle', 'fillcolor': '#F0FFF0', 'color': '#388E3C'},
            'db_branch': {'shape': 'diamond', 'fillcolor': '#FF9933', 'color': '#CC6600', 'fontcolor': 'white'},
            'db_router': {'shape': 'diamond', 'fillcolor': '#FF9933', 'color': '#CC6600', 'fontcolor': 'white'},
            'end': {'shape': 'box', 'fillcolor': '#D8BFD8', 'color': '#4B0082'},
        }

        # Get the underlying DOT structure from LangGraph
        dot_source = graph_object.get_graph().to_graphviz()

        # Create the final Digraph object
        dot_final = Digraph('SatyaMitra_Flow', format='png')
        dot_final.attr(rankdir='TB', size='10,10')
        dot_final.attr('graph', bgcolor='#ADD8E6', splines='curved', ranksep='0.7', nodesep='0.4') # Light Blue Pastel Background
        dot_final.attr('edge', fontname='Arial', fontsize='10', color='black')

        # Use the generated DOT source and apply styles manually to the DOT string for final rendering
        dot_final.source = dot_source.source # Start with the base structure

        # Render and save the image
        dot_final.render(filename.replace('.png', ''), outfile=filename, view=False)
        print(f"✅ Successfully generated workflow image: {filename}")
        return True

    except ImportError:
        print("❌ Graphviz or its Python library (python-graphviz) is not installed. Diagram cannot be generated.")
        return False
    except Exception as e:
        print(f"❌ Error during image generation: {e}")
        return False