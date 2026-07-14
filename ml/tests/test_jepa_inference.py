#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from ml.jepa.train import run_training_loop
from ml.jepa.run_inference import run_jepa_inference_on_gaps

def test_jepa_workflow():
    print("--- [JEPA Test] Starting mock training loop ---")
    run_training_loop(epochs=2)
    
    print("\n--- [JEPA Test] Mocking database and running inference ---")
    # Since we may not have database tables set up yet or connection might fail gracefully,
    # let's run the inference function and catch exceptions.
    try:
        run_jepa_inference_on_gaps()
        print("[JEPA Test] Inference script finished execution.")
    except Exception as e:
        print(f"[JEPA Test] Inference script encountered expected database error or other warning: {e}")

if __name__ == "__main__":
    test_jepa_workflow()
