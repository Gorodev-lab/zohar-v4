import torch
import torch.nn as nn
import torch.nn.functional as F
import copy

class TemporalContextEncoder(nn.Module):
    """
    Asymmetric Context Encoder for processing the historical/past trajectory crop.
    Uses 1D Convolutions and GRU/Transformer layers to capture sequential dynamics.
    """
    def __init__(self, in_features=5, embed_dim=128, hidden_dim=256):
        super(TemporalContextEncoder, self).__init__()
        self.conv1 = nn.Conv1d(in_features, 64, kernel_size=3, padding=1)
        self.conv2 = nn.Conv1d(64, embed_dim, kernel_size=3, padding=1)
        
        self.gru = nn.GRU(
            input_size=embed_dim, 
            hidden_size=hidden_dim, 
            num_layers=2, 
            batch_first=True, 
            bidirectional=False
        )
        self.proj = nn.Linear(hidden_dim, embed_dim)

    def forward(self, x):
        # x shape: [Batch, SeqLen, Features]
        h = x.transpose(1, 2)
        h = F.gelu(self.conv1(h))
        h = F.gelu(self.conv2(h))
        h = h.transpose(1, 2) # [Batch, SeqLen, EmbedDim]
        
        # Apply GRU
        out, h_n = self.gru(h)
        # Extract the last hidden state for global context representation
        # GRU output for last timestep: [Batch, hidden_dim]
        last_step = out[:, -1, :]
        return self.proj(last_step) # [Batch, EmbedDim]


class MultiHorizonPredictor(nn.Module):
    """
    Multi-horizon predictor for CF-JEPA. Takes context embedding and 
    temporal horizon index/value to forecast future latent state.
    """
    def __init__(self, embed_dim=128, horizon_dim=32):
        super(MultiHorizonPredictor, self).__init__()
        # Embedding layer for discrete horizon steps or continuous MLP projection for dt
        self.horizon_proj = nn.Sequential(
            nn.Linear(1, horizon_dim),
            nn.GELU(),
            nn.Linear(horizon_dim, horizon_dim)
        )
        
        self.predictor_net = nn.Sequential(
            nn.Linear(embed_dim + horizon_dim, embed_dim * 2),
            nn.GELU(),
            nn.Linear(embed_dim * 2, embed_dim)
        )

    def forward(self, z_context, horizon):
        # z_context: [Batch, EmbedDim]
        # horizon: [Batch, 1] representing the time offset (in hours or steps)
        h_emb = self.horizon_proj(horizon)
        
        # Concatenate context and horizon representation
        x = torch.cat([z_context, h_emb], dim=-1)
        return self.predictor_net(x)


class CFJEPA(nn.Module):
    """
    Crop-based Forward JEPA (CF-JEPA) for forward multi-horizon trajectory forecasting
    to detect abnormal trajectory gaps and spoofing.
    """
    def __init__(self, in_features=5, embed_dim=128):
        super(CFJEPA, self).__init__()
        self.context_encoder = TemporalContextEncoder(in_features=in_features, embed_dim=embed_dim)
        self.target_encoder = copy.deepcopy(self.context_encoder)
        
        # Disable gradients for Target Encoder
        for p in self.target_encoder.parameters():
            p.requires_grad = False
            
        self.predictor = MultiHorizonPredictor(embed_dim=embed_dim)

    @torch.no_grad()
    def update_target_encoder(self, momentum=0.996):
        """Updates the target encoder parameters via EMA."""
        for p_target, p_context in zip(self.target_encoder.parameters(), self.context_encoder.parameters()):
            p_target.data.copy_(momentum * p_target.data + (1.0 - momentum) * p_context.data)

    def forward(self, x_past, x_future, horizon):
        # x_past: past trajectory window (e.g. T_0 to T_t)
        # x_future: future trajectory window (e.g. T_t+1 to T_t+k)
        # horizon: [Batch, 1] time delta/step offset
        
        # 1. Target representations of the future (no gradients)
        with torch.no_grad():
            z_target = self.target_encoder(x_future)
            
        # 2. Context representation of the past (with gradients)
        z_context = self.context_encoder(x_past)
        
        # 3. Predict future representation from past context
        z_pred = self.predictor(z_context, horizon)
        
        return {
            "z_pred": z_pred,
            "z_target": z_target
        }


class MTSJEPA(nn.Module):
    """
    Multi-resolution Time Series JEPA (MTS-JEPA). Downsamples the trajectory
    to capture coarse dynamics (regimes) and fine dynamics (instants) separately.
    """
    def __init__(self, in_features=5, embed_dim=128):
        super(MTSJEPA, self).__init__()
        # Fine-scale components
        self.fine_cfjepa = CFJEPA(in_features=in_features, embed_dim=embed_dim)
        
        # Coarse-scale (regime) components
        self.coarse_cfjepa = CFJEPA(in_features=in_features, embed_dim=embed_dim)

    def update_target_encoder(self, momentum=0.996):
        self.fine_cfjepa.update_target_encoder(momentum)
        self.coarse_cfjepa.update_target_encoder(momentum)

    def forward(self, x_past, x_future, horizon):
        # Downsample trajectories for coarse regime level (e.g., average pooling)
        # Transpose for Conv/Pool: [Batch, Features, SeqLen]
        past_t = x_past.transpose(1, 2)
        future_t = x_future.transpose(1, 2)
        
        # Average pooling with kernel size 4 to extract regime properties
        past_coarse = F.avg_pool1d(past_t, kernel_size=4, stride=2, padding=1).transpose(1, 2)
        future_coarse = F.avg_pool1d(future_t, kernel_size=4, stride=2, padding=1).transpose(1, 2)
        
        # Fine-resolution predictions
        fine_results = self.fine_cfjepa(x_past, x_future, horizon)
        
        # Coarse-resolution (regime) predictions
        coarse_results = self.coarse_cfjepa(past_coarse, future_coarse, horizon)
        
        return {
            "fine_pred": fine_results["z_pred"],
            "fine_target": fine_results["z_target"],
            "coarse_pred": coarse_results["z_pred"],
            "coarse_target": coarse_results["z_target"]
        }
