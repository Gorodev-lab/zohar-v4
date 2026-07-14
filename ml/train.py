#!/usr/bin/env python3
import os
import sys
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader

# Add root directory to python path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml.loss import VICRegLoss
from ml.dataset import MaritimeMultimodalDataset, chronological_split, get_mock_db_data, get_real_db_data
from ml.models.t_jepa import TJEPA
from ml.models.cf_jepa import MTSJEPA
from ml.models.charm import CHARM, LinearProbe

def train_one_epoch(epoch, models, optimizers, loss_fn, dataloader, device, ema_momentum=0.996):
    for m in models.values():
        m.train()
        
    epoch_losses = {"total": 0.0, "tjepa": 0.0, "cfjepa": 0.0, "charm": 0.0, "probe": 0.0}
    num_batches = len(dataloader)
    
    if num_batches == 0:
        return epoch_losses

    for batch_idx, batch in enumerate(dataloader):
        # Move inputs to device
        traj = batch["trajectory"].to(device)        # [Batch, SeqLen, 5]
        text_emb = batch["text_embedding"].to(device) # [Batch, TextDim]
        bio_dens = batch["bio_density"].to(device)   # [Batch, SeqLen, 1]
        
        # Zero gradients
        for opt in optimizers.values():
            opt.zero_grad()

        # ----------------------------------------------------
        # 1. T-JEPA Training Step
        # ----------------------------------------------------
        tjepa_res = models["tjepa"](traj)
        z_pred_t = tjepa_res["z_pred"]
        z_target_t = tjepa_res["z_target"]
        mask_t = tjepa_res["mask"]
        
        # Only evaluate loss on masked target items to prevent shortcutting
        mask_expanded = mask_t.unsqueeze(-1).expand_as(z_pred_t)
        tjepa_loss = loss_fn(z_pred_t * (1 - mask_expanded), z_target_t * (1 - mask_expanded))
        
        # ----------------------------------------------------
        # 2. CF-JEPA / MTS-JEPA Training Step
        # ----------------------------------------------------
        # Split trajectory into past (first 60%) and future (remaining 40%)
        seq_len = traj.size(1)
        split_pt = int(seq_len * 0.6)
        
        x_past = traj[:, :split_pt, :]
        x_future = traj[:, split_pt:, :]
        
        # Horizon is the temporal index difference (e.g. elapsed hours between past and future)
        # For mock, we pass a constant offset or calculate from the timestamp difference feature
        horizon = traj[:, split_pt, 4:5] * (seq_len - split_pt) # [Batch, 1]
        
        mtsjepa_res = models["cfjepa"](x_past, x_future, horizon)
        
        # We calculate VICReg loss for both fine and coarse resolution predictions
        fine_loss = loss_fn(mtsjepa_res["fine_pred"], mtsjepa_res["fine_target"])
        coarse_loss = loss_fn(mtsjepa_res["coarse_pred"], mtsjepa_res["coarse_target"])
        cfjepa_loss = fine_loss + 0.5 * coarse_loss

        # ----------------------------------------------------
        # 3. CHARM Multimodal Fusion Training Step
        # ----------------------------------------------------
        z_fused, z_global = models["charm"](traj, text_emb, bio_dens)
        
        # Self-supervised objective for CHARM: reconstruct the trajectory sequence from fused embeddings
        # We project the fused embeddings back to trajectory dimensions for prediction matching
        reconstructed_traj = models["charm_recon"](z_fused)
        charm_loss = F.mse_loss(reconstructed_traj, traj)

        # ----------------------------------------------------
        # 4. Linear Probe Validation Step
        # ----------------------------------------------------
        # We evaluate frozen embeddings of CHARM to predict a target attribute
        # For mock, we predict whether the average speed of the vessel exceeds 12 knots (binary target)
        avg_speed = traj[:, :, 2].mean(dim=1, keepdim=True)
        probe_target = (avg_speed > 12.0).float().to(device)
        
        probe_pred = models["probe"](z_global)
        probe_loss = F.binary_cross_entropy_with_logits(probe_pred, probe_target)

        # ----------------------------------------------------
        # Backpropagation and Updates
        # ----------------------------------------------------
        # Compute backward pass
        total_loss = tjepa_loss + cfjepa_loss + charm_loss + probe_loss
        total_loss.backward()
        
        # Perform optimizer step
        for opt in optimizers.values():
            opt.step()
            
        # Update EMA target encoders for TJEPA and CFJEPA
        models["tjepa"].update_target_encoder(ema_momentum)
        models["cfjepa"].update_target_encoder(ema_momentum)
        
        # Accumulate losses
        epoch_losses["total"] += total_loss.item()
        epoch_losses["tjepa"] += tjepa_loss.item()
        epoch_losses["cfjepa"] += cfjepa_loss.item()
        epoch_losses["charm"] += charm_loss.item()
        epoch_losses["probe"] += probe_loss.item()
        
    # Average losses
    for k in epoch_losses.keys():
        epoch_losses[k] /= num_batches
        
    return epoch_losses


def main():
    print("====================================================")
    print("        LOGR SSL JEPA Training Orchestrator")
    print("====================================================")
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[Device] Using device: {device}")
    
    # 1. Load data
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
    print(f"[Data] Attempting to load dataset from SQL database...")
    try:
        db_data = get_real_db_data(db_url)
        print(f"[Data] Loaded real database data successfully ({len(db_data)} vessels).")
    except Exception as e:
        print(f"[Data] Database loading failed: {e}. Falling back to mock data.")
        db_data = get_mock_db_data(num_vessels=10, points_per_vessel=200)

    train_split, val_split = chronological_split(db_data, split_ratio=0.8)
    
    train_dataset = MaritimeMultimodalDataset(*train_split, seq_len=64)
    val_dataset = MaritimeMultimodalDataset(*val_split, seq_len=64)
    
    train_loader = DataLoader(train_dataset, batch_size=4, shuffle=True, drop_last=True)
    val_loader = DataLoader(val_dataset, batch_size=4, shuffle=False)
    
    print(f"[Data] Train samples: {len(train_dataset)}, Validation samples: {len(val_dataset)}")
    
    # 2. Initialize Models
    embed_dim = 128
    models = {
        "tjepa": TJEPA(in_features=5, embed_dim=embed_dim).to(device),
        "cfjepa": MTSJEPA(in_features=5, embed_dim=embed_dim).to(device),
        "charm": CHARM(kin_features=5, text_dim=128, bio_features=1, embed_dim=embed_dim).to(device),
        "charm_recon": nn.Linear(embed_dim, 5).to(device), # simple projection for trajectory reconstruction
        "probe": LinearProbe(representation_dim=embed_dim, target_dim=1).to(device)
    }
    
    # 3. Setup Optimizers
    optimizers = {
        "tjepa": optim.AdamW(models["tjepa"].parameters(), lr=1e-3, weight_decay=1e-4),
        "cfjepa": optim.AdamW(models["cfjepa"].parameters(), lr=1e-3, weight_decay=1e-4),
        "charm": optim.AdamW(
            list(models["charm"].parameters()) + list(models["charm_recon"].parameters()), 
            lr=1e-3, weight_decay=1e-4
        ),
        "probe": optim.AdamW(models["probe"].parameters(), lr=2e-3)
    }
    
    # 4. Initialize Loss
    loss_fn = VICRegLoss(sim_weight=25.0, var_weight=25.0, cov_weight=1.0).to(device)
    
    # 5. Training loop
    epochs = 3
    print(f"[Train] Starting SSL training for {epochs} epochs...")
    
    for epoch in range(1, epochs + 1):
        losses = train_one_epoch(
            epoch, models, optimizers, loss_fn, train_loader, device
        )
        print(f"Epoch {epoch:02d} | Loss: {losses['total']:.4f} | "
              f"T-JEPA: {losses['tjepa']:.4f} | CF-JEPA: {losses['cfjepa']:.4f} | "
              f"CHARM: {losses['charm']:.4f} | Probe: {losses['probe']:.4f}")

    print("[Train] Training completed successfully!")
    
    # Save model checkpoint
    os.makedirs("dw/checkpoints", exist_ok=True)
    checkpoint_path = "dw/checkpoints/jepa_ssl_checkpoint.pt"
    torch.save({
        "tjepa_state_dict": models["tjepa"].state_dict(),
        "cfjepa_state_dict": models["cfjepa"].state_dict(),
        "charm_state_dict": models["charm"].state_dict(),
        "probe_state_dict": models["probe"].state_dict()
    }, checkpoint_path)
    print(f"[Store] Saved model weights to {checkpoint_path}")

if __name__ == "__main__":
    main()
