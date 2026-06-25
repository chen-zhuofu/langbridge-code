"""A minimal Transformer encoder/decoder style module for synthetic-env.

This module intentionally keeps the implementation small so it can serve as a
building block for experiments that do not want to depend on the Transformers
library.  Only the functionality required by the synthetic environment is
implemented: token + positional embeddings, a stack of Transformer blocks, and
an output projection back to the vocabulary size.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import torch
from torch import nn, Tensor
from torch.nn import functional as F


@dataclass(slots=True)
class TransformerConfig:
    """Configuration container for :class:`TransformerModule`.

    Attributes:
        vocab_size: Size of the token vocabulary.
        max_seq_len: Maximum supported sequence length.
        embed_dim: Embedding dimension.
        num_heads: Number of attention heads.
        num_layers: Number of stacked Transformer blocks.
        mlp_ratio: Hidden size multiplier inside the feed-forward block.
        dropout: Dropout probability applied after embeddings and inside blocks.
    """

    vocab_size: int
    max_seq_len: int
    embed_dim: int
    num_heads: int
    num_layers: int
    mlp_ratio: float = 4.0
    dropout: float = 0.1


class TransformerBlock(nn.Module):
    """Single Transformer encoder block with pre-norm architecture."""

    def __init__(self, embed_dim: int, num_heads: int, mlp_ratio: float, dropout: float) -> None:
        super().__init__()
        self.attn = nn.MultiheadAttention(
            embed_dim,
            num_heads,
            dropout=dropout,
            batch_first=True,
        )
        hidden_dim = int(embed_dim * mlp_ratio)
        self.linear1 = nn.Linear(embed_dim, hidden_dim)
        self.linear2 = nn.Linear(hidden_dim, embed_dim)
        self.norm1 = nn.LayerNorm(embed_dim)
        self.norm2 = nn.LayerNorm(embed_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: Tensor, key_padding_mask: Optional[Tensor] = None) -> Tensor:
        attn_input = self.norm1(x)
        attn_output, _ = self.attn(
            attn_input,
            attn_input,
            attn_input,
            key_padding_mask=key_padding_mask,
            need_weights=False,
        )
        x = x + self.dropout(attn_output)

        ff_input = self.norm2(x)
        ff = self.linear2(self.dropout(F.gelu(self.linear1(ff_input))))
        return x + self.dropout(ff)


class TransformerModule(nn.Module):
    """A tiny Transformer for sequence-to-sequence style modeling."""

    def __init__(self, config: TransformerConfig) -> None:
        super().__init__()
        self.config = config
        self.token_embedding = nn.Embedding(config.vocab_size, config.embed_dim)
        self.position_embedding = nn.Embedding(config.max_seq_len, config.embed_dim)
        self.dropout = nn.Dropout(config.dropout)
        self.blocks = nn.ModuleList(
            [
                TransformerBlock(
                    config.embed_dim,
                    config.num_heads,
                    config.mlp_ratio,
                    config.dropout,
                )
                for _ in range(config.num_layers)
            ]
        )
        self.norm = nn.LayerNorm(config.embed_dim)
        self.output = nn.Linear(config.embed_dim, config.vocab_size)

    def forward(self, input_ids: Tensor, attention_mask: Optional[Tensor] = None) -> Tensor:
        """Run the Transformer and return logits over the vocabulary.

        Args:
            input_ids: Tensor of shape (batch, seq_len) with token ids.
            attention_mask: Optional tensor of the same shape where 1 marks real
                tokens and 0 marks padding. If ``None`` all tokens attend.
        """

        if input_ids.dim() != 2:
            raise ValueError("input_ids must be rank 2: (batch, seq_len)")
        batch_size, seq_len = input_ids.shape
        if seq_len > self.config.max_seq_len:
            raise ValueError("Sequence length exceeds configured maximum")

        device = input_ids.device
        positions = torch.arange(seq_len, device=device).unsqueeze(0).expand(batch_size, seq_len)
        x = self.token_embedding(input_ids) + self.position_embedding(positions)
        x = self.dropout(x)

        key_padding_mask = None
        if attention_mask is not None:
            if attention_mask.shape != input_ids.shape:
                raise ValueError("attention_mask must match input_ids shape")
            key_padding_mask = attention_mask == 0

        for block in self.blocks:
            x = block(x, key_padding_mask=key_padding_mask)

        x = self.norm(x)
        return self.output(x)


__all__ = ["TransformerConfig", "TransformerModule"]
