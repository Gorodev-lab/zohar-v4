import os
import sys
import math
import torch
import torch.nn as nn
import torch.optim as optim
try:
    from ml.jepa.encoder import TrajectoryEncoder, AcousticEncoder
    from ml.jepa.predictor import LatentPredictor
except ImportError:
    from encoder import TrajectoryEncoder, AcousticEncoder
    from predictor import LatentPredictor

def get_device():
    """
    Utility to choose device. Checks Vulkan availability first, then CPU.
    Note: CUDA is explicitly bypassed per user instructions.
    """
    if torch.is_vulkan_available():
        print("[Device] Vulkan acceleration backend found. Targeting 'vulkan'.")
        return torch.device('vulkan')
    else:
        print("[Device] Vulkan not available. Falling back to 'cpu' (MKL/OpenMP optimized).")
        return torch.device('cpu')

def compute_cylindrical_propagation_loss(r, alpha=0.0015):
    """
    Deterministic physical loss for cylindrical acoustics in shallow waters:
    TL = 10 * log10(r) + alpha * r
    """
    if r <= 0:
        return 0.0
    return 10.0 * math.log10(r) + alpha * r

def train_jepa_step(context_encoder, target_encoder, predictor, optimizer, 
                     context_x, target_y, gap_params, device):
    """
    Executes a single self-supervised JEPA training step.
    Computes embedding representations and minimizes distance in latent space.
    """
    optimizer.zero_grad()
    
    # 1. Map inputs to latent representations (detach target encoder to prevent representation collapse)
    with torch.no_grad():
        target_representation = target_encoder(target_y.to(device))
        
    context_representation = context_encoder(context_x.to(device))
    
    # 2. Predict target representation from context and actions/parameters
    predicted_representation = predictor(context_representation, gap_params.to(device))
    
    # 3. Calculate latent prediction loss (MSE or L2 distance)
    # Target embeddings are normalized, so distance minimization maximizes cosine similarity
    loss_latent = nn.functional.mse_loss(predicted_representation, target_representation)
    
    # 4. Physics-informed regularization for acoustics (if sound propagation is modeled)
    # We enforce that the distance in embedding space correlates with physical transmission loss
    # Let's say for a mock pair, we extract distance 'r' from the gap parameters
    r_vals = gap_params[:, 1] # assuming index 1 is distance
    physics_penalty = 0.0
    for i in range(len(r_vals)):
        r = float(r_vals[i].item())
        if r > 0:
            physical_tl = compute_cylindrical_propagation_loss(r)
            # Simulated: higher propagation loss should translate to lower cosine similarity (higher distance)
            sim_cos = torch.dot(context_representation[i], target_representation[i])
            # We construct a soft physics constraint: similarity should decay with 1 / (1 + TL/10)
            expected_sim = 1.0 / (1.0 + (physical_tl / 10.0))
            physics_penalty += nn.functional.l1_loss(sim_cos, torch.tensor(expected_sim, device=device))
            
    total_loss = loss_latent + 0.1 * (physics_penalty / len(r_vals))
    
    total_loss.backward()
    optimizer.step()
    
    return total_loss.item()

def run_training_loop(epochs=5):
    device = get_device()
    
    # Initialize networks
    context_encoder = TrajectoryEncoder().to(device)
    target_encoder = TrajectoryEncoder().to(device)
    predictor = LatentPredictor(action_dim=2).to(device)
    
    optimizer = optim.Adam(
        list(context_encoder.parameters()) + list(predictor.parameters()), 
        lr=0.001
    )
    
    print(f"[JEPA Train] Starting self-supervised training loop for {epochs} epochs...")
    
    # Generate dummy mock dataset representing trajectory sequences
    # Sequence of 12 points, 4 dimensions [lat, lon, speed, course]
    batch_size = 16
    mock_context = torch.randn(batch_size, 12, 4)
    mock_target = torch.randn(batch_size, 12, 4)
    # Gap parameters: [gap_duration_hours, distance_meters]
    mock_gap_params = torch.rand(batch_size, 2) * 10.0
    
    for epoch in range(epochs):
        loss = train_jepa_step(
            context_encoder, target_encoder, predictor, optimizer,
            mock_context, mock_target, mock_gap_params, device
        )
        print(f"Epoch {epoch+1}/{epochs} - Loss: {loss:.6f}")
        
    print("[JEPA Train] Training completed successfully!")
    
    # Save the models
    os.makedirs("ml/weights", exist_ok=True)
    torch.save(context_encoder.state_dict(), "ml/weights/trajectory_encoder.pt")
    torch.save(predictor.state_dict(), "ml/weights/latent_predictor.pt")
    print("[JEPA Train] Saved weights to ml/weights/")

if __name__ == "__main__":
    run_training_loop()
