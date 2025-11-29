import uvicorn
import json
import sqlite3
import datetime
from fastapi import FastAPI, Form, Response, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List
from contextlib import asynccontextmanager
from urllib.parse import urlparse

# IMPORT YOUR AGENT BRAIN AND IMAGE GENERATION FUNCTION
from agent import satyamitra_brain, generate_workflow_image, DIAGRAM_PATH 

# --- LIFESPAN MANAGER ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 SatyaMitra API is waking up...")
    
    # Generate the workflow image on startup (Calls the function in agent.py)
    generate_workflow_image(satyamitra_brain, filename=DIAGRAM_PATH)
    
    # Initialize DB tables for persistence and analytics
    conn = sqlite3.connect("satyamitra.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS domain_reputation (
            domain TEXT PRIMARY KEY, 
            status TEXT, 
            confidence INT
        )
    """)
    # UPDATED verification_history schema with origin and user_role
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS verification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            claim_text TEXT,  
            verdict TEXT,
            origin_city TEXT,       
            origin_country TEXT,    
            user_role TEXT,         
            timestamp DATETIME
        )
    """)
    # NEW source_logs schema
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
    conn.commit()
    conn.close()
    
    yield
    print("💤 SatyaMitra API is going to sleep...")

app = FastAPI(title="SatyaMitra API", lifespan=lifespan)

# --- DATA MODELS ---
class WebRequest(BaseModel):
    text: str
    user_id: str = "web_user"
    input_type: str = "text"
    image_data: str | None = None
    user_role: str = "standard" 

# --- ENDPOINT 1: THE WEB API (STREAMING) ---
@app.post("/verify")
async def verify_news(request: WebRequest):
    print(f"📩 Web Request: {request.text} | Type: {request.input_type} | Role: {request.user_role}")
    
    # 1. Prepare Input (LangGraph's initial state)
    initial_state = {
        "messages": [("user", request.text)],
        "revision_count": 0,
        "is_verified": False,
        "verdict": "PENDING",
        "input_type": request.input_type,
        "image_data": request.image_data,
        "user_role": request.user_role 
    }
    
    config = {"configurable": {"thread_id": request.user_id}}

    # 2. Define the Stream Generator
    async def event_generator():
        final_verdict = "UNVERIFIED"
        
        try:
            # Loop through the graph events as they happen
            async for event in satyamitra_brain.astream(initial_state, config):
                
                # Check for node completion (researcher, skeptic, reporter)
                for node_name, node_output in event.items():
                    if node_name in ["start", "pre_processor", "db_analyst", "researcher", "skeptic", "reporter", "end"]:
                        
                        # Extract content safely
                        msg = node_output["messages"][-1]
                        content = msg.content if hasattr(msg, "content") else str(msg)
                        
                        # Set a status message based on the agent type
                        status_map = {
                            "start": "🎯 Initiating Agent Workflow...",
                            "pre_processor": "🧠 PreProcessor Agent is pre-processing the claim...",
                            "db_analyst": "💾 DB Analyst is checking internal archives (MCP)...",
                            "researcher": "🕵️ Researcher Agent is researching the claim...",
                            "skeptic": "⚖️ Skeptic Agent is evaluating the evidence quality...",
                            "reporter": "📝 Reporter Agent is drafting final report...",
                            "end": "Agent Workflow complete..."
                        }
                        
                        # Send status and details (for the Audit Trace)
                        yield json.dumps({
                            "type": "step", 
                            "status": status_map.get(node_name, f"Processing {node_name}..."),
                            "details": content,
                            "active_node": node_name
                        }) + "\n"
                        
                        # If the reporter node runs, capture the final verdict
                        if node_name == "reporter":
                            final_verdict = content
                            yield json.dumps({"type": "result", "verdict": final_verdict}) + "\n"
                            break 

        except Exception as e:
            error_message = f"Critical Agent Error: {type(e).__name__}: {e}"
            print(f"❌ Stream Error: {error_message}")
            yield json.dumps({"type": "error", "message": error_message}) + "\n"

    # 3. Return as a Stream (NDJSON format)
    return StreamingResponse(event_generator(), media_type="application/x-ndjson")

# --- ENDPOINT 2: THE WHATSAPP HOOK (Twilio) ---
@app.post("/whatsapp")
async def whatsapp_reply(Body: str = Form(...), From: str = Form(...)):
    print(f"📲 WhatsApp from {From}: {Body}")
    
    initial_state = {
        "messages": [("user", Body)],
        "revision_count": 0,
        "is_verified": False,
        "verdict": "PENDING",
        "input_type": "text", 
        "image_data": None,
        "user_role": "standard" 
    }
    
    config = {"configurable": {"thread_id": From}}
    
    try:
        output = await satyamitra_brain.ainvoke(initial_state, config)
        last_msg = output["messages"][-1]
        bot_reply = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    except Exception as e:
        bot_reply = f"System Error. Please try again later. ({type(e).__name__})"
    
    # TwiML XML Response
    xml_response = f"""
    <Response>
        <Message>
            {bot_reply}
        </Message>
    </Response>
    """
    return Response(content=xml_response, media_type="application/xml")


# --- ENDPOINT 3: ANALYTICS (Data Access) ---
@app.get("/analytics")
async def get_analytics():
    try:
        conn = sqlite3.connect("satyamitra.db")
        conn.row_factory = sqlite3.Row 
        cursor = conn.cursor()

        # Fetch basic metrics
        total_verifications = cursor.execute("SELECT COUNT(*) FROM verification_history").fetchone()[0]
        verdict_counts = cursor.execute("SELECT verdict, COUNT(*) FROM verification_history GROUP BY verdict").fetchall()
        
        # Fetch data for new metrics
        recent_verifications = cursor.execute(
            "SELECT id, user_id, claim_text, verdict, timestamp FROM verification_history ORDER BY timestamp DESC LIMIT 5"
        ).fetchall()

        # --- NEW METRICS QUERIES ---
        
        # 1. Origin of Claim (City, Country)
        origin_counts = cursor.execute("""
            SELECT 
                origin_city, origin_country, COUNT(*) AS count
            FROM verification_history
            GROUP BY origin_city, origin_country
            ORDER BY count DESC
        """).fetchall()

        # 2. Source Accuracy (Website/Source vs. Verdict)
        source_accuracy = cursor.execute("""
            SELECT 
                source_identifier, verdict, COUNT(*) as count
            FROM source_logs
            GROUP BY source_identifier, verdict
        """).fetchall()

        # 3. User Role Breakdown
        user_role_counts = cursor.execute("""
            SELECT user_role, COUNT(*) as count FROM verification_history GROUP BY user_role
        """).fetchall()

        # 4. Hourly Distribution (Peak Usage)
        hourly_counts = cursor.execute("""
            SELECT STRFTIME('%H', timestamp) as hour, COUNT(*) as count 
            FROM verification_history 
            GROUP BY hour ORDER BY hour
        """).fetchall()
        
        conn.close()

        # --- Data Aggregation ---
        verdict_breakdown = {row['verdict']: row['COUNT(*)'] for row in verdict_counts}
        all_verdicts = ["TRUE", "FALSE", "MISLEADING", "UNVERIFIED"]
        for v in all_verdicts:
            if v not in verdict_breakdown:
                verdict_breakdown[v] = 0

        source_accuracy_breakdown = {}
        for row in source_accuracy:
            source = row['source_identifier']
            verdict = row['verdict']
            count = row['count']
            if source not in source_accuracy_breakdown:
                source_accuracy_breakdown[source] = {}
            source_accuracy_breakdown[source][verdict] = count

        return {
            "total_verifications": total_verifications,
            "verdict_breakdown": verdict_breakdown,
            "recent_verifications": [
                {k: item[k] for k in item.keys()} for item in recent_verifications
            ],
            "origin_of_claim": [{"city": row['origin_city'], "country": row['origin_country'], "count": row['count']} for row in origin_counts],
            "source_accuracy_breakdown": source_accuracy_breakdown,
            "user_role_breakdown": {row['user_role']: row['count'] for row in user_role_counts},
            "hourly_counts": {row['hour']: row['count'] for row in hourly_counts}
        }
    except Exception as e:
        print(f"❌ ANALYTICS DB ERROR: {e}")
        raise HTTPException(status_code=500, detail=f"Database Analytics Error: {e}")

# --- ENDPOINT 4: AUDIT LOG DELETE (RBAC Protected) ---
class DeleteRequest(BaseModel):
    ids: List[int]
    user_role: str

@app.post("/audit/delete")
async def delete_logs(request: DeleteRequest):
    if request.user_role != 'admin':
        raise HTTPException(status_code=403, detail="Permission Denied: Only Admin users can delete audit logs.")
    
    if not request.ids:
        return {"status": "success", "message": "No IDs provided."}

    try:
        conn = sqlite3.connect("satyamitra.db")
        cursor = conn.cursor()
        
        placeholders = ','.join('?' for _ in request.ids)
        
        # NOTE: Deleting from verification_history should cascade in a real DB, 
        # but in SQLite, we handle manually if needed, or rely on FOREIGN KEY constraints
        # which are often soft in SQLite unless explicitly enabled. For simplicity, just delete main log.
        cursor.execute(f"DELETE FROM verification_history WHERE id IN ({placeholders})", request.ids)
        rows_deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        return {"status": "success", "message": f"Successfully deleted {rows_deleted} log(s)."}
        
    except Exception as e:
        print(f"❌ DELETE DB ERROR: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete logs: {e}")


# --- RUNNER ---
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)