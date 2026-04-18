from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Try to load a local model
MODEL_LOADED = False
model = None

try:
    from transformers import pipeline, AutoModelForCausalLM, AutoTokenizer
    import torch
    
    # Use a small, fast model for fallback
    MODEL_NAME = os.getenv("LOCAL_MODEL", "distilgpt2")
    
    print(f"Loading local model: {MODEL_NAME}")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
    
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    
    MODEL_LOADED = True
    print("Local model loaded successfully!")
except Exception as e:
    print(f"Could not load local model: {e}")
    MODEL_LOADED = False

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "model_loaded": MODEL_LOADED
    })

@app.route("/ai/local-evaluate", methods=["POST"])
def local_evaluate():
    data = request.json
    prompt = data.get("prompt", "")
    
    if not MODEL_LOADED:
        return jsonify({
            "error": "Model not loaded",
            "result": '{"result": "partial", "score_pct": 50, "critique": "Local model unavailable"}'
        }), 500
    
    try:
        inputs = tokenizer(prompt, return_tensors="pt", max_length=512, truncation=True)
        
        with torch.no_grad():
            outputs = model.generate(
                inputs.input_ids,
                max_new_tokens=200,
                do_sample=True,
                temperature=0.7,
                pad_token_id=tokenizer.pad_token_id
            )
        
        result = tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # Extract JSON from result
        import re
        json_match = re.search(r'\{[^{}]*\}', result)
        if json_match:
            return jsonify({"result": json_match.group()})
        
        # Fallback JSON
        return jsonify({
            "result": '{"result": "partial", "score_pct": 50, "critique": "Could not parse local response"}'
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(port=5001, debug=False)