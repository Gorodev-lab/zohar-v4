import torch
import torch.nn as nn
import torch.nn.functional as F

class TCNResidualBlock(nn.Module):
    """
    Residual block for Temporal Convolutional Network (TCN) to handle time-series
    kinematics with dilation and causal padding.
    """
    def __init__(self, in_channels, out_channels, dilation, kernel_size=3, dropout=0.2):
        super(TCNResidualBlock, self).__init__()
        # Causal padding = (kernel_size - 1) * dilation
        padding = (kernel_size - 1) * dilation
        
        self.conv1 = nn.Conv1d(
            in_channels, out_channels, kernel_size, 
            padding=padding, dilation=dilation
        )
        self.dropout1 = nn.Dropout(dropout)
        
        self.conv2 = nn.Conv1d(
            out_channels, out_channels, kernel_size, 
            padding=padding, dilation=dilation
        )
        self.dropout2 = nn.Dropout(dropout)
        
        # Adjust residual shortcut if dimensions differ
        self.shortcut = nn.Conv1d(in_channels, out_channels, 1) if in_channels != out_channels else None
        
        self.padding = padding

    def forward(self, x):
        # x shape: [Batch, Channels, SeqLen]
        # First layer
        out = F.gelu(self.conv1(x))
        # Causal cropping of padding from the right side of the sequence dimension
        out = out[:, :, :-self.padding]
        out = self.dropout1(out)
        
        # Second layer
        out = F.gelu(self.conv2(out))
        out = out[:, :, :-self.padding]
        out = self.dropout2(out)
        
        res = x if self.shortcut is None else self.shortcut(x)
        return F.gelu(out + res)


class TemporalConvNet(nn.Module):
    """Temporal Convolutional Network for encoding kinematics."""
    def __init__(self, in_features=5, num_channels=[64, 128, 128], kernel_size=3, dropout=0.2):
        super(TemporalConvNet, self).__init__()
        layers = []
        num_levels = len(num_channels)
        for i in range(num_levels):
            dilation_size = 2 ** i
            in_c = in_features if i == 0 else num_channels[i - 1]
            out_c = num_channels[i]
            layers.append(TCNResidualBlock(in_c, out_c, dilation_size, kernel_size, dropout))
            
        self.network = nn.Sequential(*layers)

    def forward(self, x):
        # Input shape: [Batch, SeqLen, Features] -> Transpose for Conv1d: [Batch, Features, SeqLen]
        h = x.transpose(1, 2)
        h = self.network(h)
        return h.transpose(1, 2) # [Batch, SeqLen, OutChannels]


class CHARM(nn.Module):
    """
    Channel-Aware Representation Model (CHARM) for multimodal telemetry-metadata fusion.
    Processes kinematics (TCN), texts (MLP), and biology densities (MLP) as distinct
    channels and fuses them using Multihead Attention.
    """
    def __init__(self, kin_features=5, text_dim=128, bio_features=1, embed_dim=128, num_heads=4):
        super(CHARM, self).__init__()
        self.embed_dim = embed_dim
        
        # 1. Kinematics Channel (TCN)
        self.kin_encoder = TemporalConvNet(in_features=kin_features, num_channels=[64, 128, embed_dim])
        
        # 2. Text Channel (Permit text projection)
        self.text_encoder = nn.Sequential(
            nn.Linear(text_dim, embed_dim),
            nn.GELU(),
            nn.Linear(embed_dim, embed_dim)
        )
        
        # 3. Biology Channel (OBIS density projection)
        self.bio_encoder = nn.Sequential(
            nn.Linear(bio_features, embed_dim),
            nn.GELU(),
            nn.Linear(embed_dim, embed_dim)
        )
        
        # 4. Multihead Attention for Channel-Aware Cross-Modal Fusion
        self.cross_attn = nn.MultiheadAttention(embed_dim=embed_dim, num_heads=num_heads, batch_first=True)
        self.norm = nn.LayerNorm(embed_dim)
        
        # 5. Output Projection
        self.fc_fused = nn.Sequential(
            nn.Linear(embed_dim, embed_dim),
            nn.GELU(),
            nn.Linear(embed_dim, embed_dim)
        )

    def forward(self, trajectory, text_embedding, bio_density):
        # trajectory: [Batch, SeqLen, 5]
        # text_embedding: [Batch, TextDim]
        # bio_density: [Batch, SeqLen, 1]
        batch_size, seq_len, _ = trajectory.size()
        
        # Encode Channels
        # Kinematics Channel: [Batch, SeqLen, EmbedDim]
        ch_kin = self.kin_encoder(trajectory)
        
        # Text Channel: [Batch, SeqLen, EmbedDim] (Replicate text representation across the sequence)
        z_text = self.text_encoder(text_embedding) # [Batch, EmbedDim]
        ch_text = z_text.unsqueeze(1).expand(-1, seq_len, -1)
        
        # Biology Channel: [Batch, SeqLen, EmbedDim]
        ch_bio = self.bio_encoder(bio_density)
        
        # Cross-Attention: align kinematics query with text/bio keys & values
        # Concatenate keys and values: shape [Batch, 2 * SeqLen, EmbedDim]
        keys_vals = torch.cat([ch_text, ch_bio], dim=1)
        
        # Attention: query=ch_kin, key=keys_vals, value=keys_vals
        attn_out, _ = self.cross_attn(query=ch_kin, key=keys_vals, value=keys_vals)
        
        # Residual connection and layer norm
        fused = self.norm(ch_kin + attn_out) # [Batch, SeqLen, EmbedDim]
        
        # Project to final representation
        z_fused = self.fc_fused(fused)
        
        # Global trajectory representation by pooling across sequence length
        z_global = z_fused.mean(dim=1) # [Batch, EmbedDim]
        
        return z_fused, z_global


class LinearProbe(nn.Module):
    """
    Linear Probe for evaluating frozen representations of CHARM.
    Used to demonstrate how frozen embeddings correlate textual/biological signals 
    with raw GPS trajectory properties without modifying the backbone.
    """
    def __init__(self, representation_dim=128, target_dim=1):
        super(LinearProbe, self).__init__()
        # Single linear layer (probe)
        self.classifier = nn.Linear(representation_dim, target_dim)

    def forward(self, x):
        # x is the frozen representation: [Batch, RepresentationDim]
        # We detach x to ensure no gradients propagate to the backbone
        x_frozen = x.detach()
        return self.classifier(x_frozen)
