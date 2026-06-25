"""Minimal Transformer implementation with RMSNorm for synthetic-env."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import torch
from torch import Tensor, nn


class RMSNorm(nn.Module):
    """Root mean square normalization used in some modern Transformers."""

    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: Tensor) -> Tensor:
        norm = torch.mean(x.pow(2), dim=-1, keepdim=True)
        return self.weight * x * torch.rsqrt(norm + self.eps)


@dataclass(slots=True)
class TransformerConfig:
    """Configuration for :class:`TransformerModel`."""

    vocab_size: int
    max_seq_len: int
    embed_dim: int
    num_heads: int
    num_layers: int
    mlp_ratio: float = 4.0
    dropout: float = 0.1
    attn_dropout: float = 0.0
    causal: bool = True


class TransformerBlock(nn.Module):
    """A single Transformer block with RMSNorm and residual connections."""

    def __init__(self, config: TransformerConfig) -> None:
        super().__init__()
        self.attn = nn.MultiheadAttention(
            embed_dim=config.embed_dim,
            num_heads=config.num_heads,
            dropout=config.attn_dropout,
            batch_first=True,
        )
        hidden = int(config.embed_dim * config.mlp_ratio)
        self.linear1 = nn.Linear(config.embed_dim, hidden)
        self.linear2 = nn.Linear(hidden, config.embed_dim)
        self.dropout = nn.Dropout(config.dropout)
        self.norm1 = RMSNorm(config.embed_dim)
        self.norm2 = RMSNorm(config.embed_dim)

    def forward(
        self,
        x: Tensor,
        key_padding_mask: Optional[Tensor],
        attn_mask: Optional[Tensor],
    ) -> Tensor:
        attn_input = self.norm1(x)
        attn_output, _ = self.attn(
            attn_input,
            attn_input,
            attn_input,
            key_padding_mask=key_padding_mask,
            attn_mask=attn_mask,
            need_weights=False,
        )
        x = x + self.dropout(attn_output)
        ff_input = self.norm2(x)
        ff_output = self.linear2(self.dropout(torch.nn.functional.gelu(self.linear1(ff_input))))
        return x + self.dropout(ff_output)


class TransformerModel(nn.Module):
    """Tiny Transformer suitable for experiments inside synthetic-env."""

    def __init__(self, config: TransformerConfig) -> None:
        super().__init__()
        self.config = config
        self.token_embedding = nn.Embedding(config.vocab_size, config.embed_dim)
        self.position_embedding = nn.Embedding(config.max_seq_len, config.embed_dim)
        self.dropout = nn.Dropout(config.dropout)
        self.blocks = nn.ModuleList([TransformerBlock(config) for _ in range(config.num_layers)])
        self.final_norm = RMSNorm(config.embed_dim)
        self.lm_head = nn.Linear(config.embed_dim, config.vocab_size, bias=False)

    def _build_causal_mask(self, seq_len: int, device: torch.device) -> Tensor:
        mask = torch.full((seq_len, seq_len), float("-inf"), device=device)
        return torch.triu(mask, diagonal=1)

    def forward(self, input_ids: Tensor, attention_mask: Optional[Tensor] = None) -> Tensor:
        if input_ids.dim() != 2:
            raise ValueError("input_ids must be of shape (batch, seq_len)")
        batch, seq_len = input_ids.shape
        if seq_len > self.config.max_seq_len:
            raise ValueError("Sequence length exceeds max_seq_len")

        device = input_ids.device
        positions = torch.arange(seq_len, device=device).unsqueeze(0).expand(batch, seq_len)
        x = self.token_embedding(input_ids) + self.position_embedding(positions)
        x = self.dropout(x)

        key_padding_mask = None
        if attention_mask is not None:
            if attention_mask.shape != input_ids.shape:
                raise ValueError("attention_mask must match input_ids shape")
            key_padding_mask = attention_mask == 0

        attn_mask = None
        if self.config.causal:
            attn_mask = self._build_causal_mask(seq_len, device)

        for block in self.blocks:
            x = block(x, key_padding_mask=key_padding_mask, attn_mask=attn_mask)

        x = self.final_norm(x)
        return self.lm_head(x)
