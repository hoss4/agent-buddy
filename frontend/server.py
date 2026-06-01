from flask import Flask, jsonify, request, send_file
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from src.database.db_utils import get_all_pending_hitl, submit_hitl_decision
import json

app = Flask(__name__)


@app.route("/")
def index():
    return send_file("index.html")


@app.route("/api/pending")
def pending():
    """Returns all pending HITL requests."""
    requests = get_all_pending_hitl()
    # Parse options_json into actual objects
    for r in requests:
        r["options"] = json.loads(r["options_json"])
        del r["options_json"]
    return jsonify(requests)


@app.route("/api/decide/<int:request_id>", methods=["POST"])
def decide(request_id):
    """User submits their choice."""
    data = request.get_json()
    chosen_key = data.get("chosen_key", "").upper()
    
    if not chosen_key:
        return jsonify({"error": "missing chosen_key"}), 400
    
    success = submit_hitl_decision(request_id, chosen_key)
    if not success:
        return jsonify({"error": "request not found or already answered"}), 404
    
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)