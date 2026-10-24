"""A small Transformer encoder, following Vaswani et al. 2017 ("Attention
Is All You Need"), built on the autodiff engine in `autodiff.py`.

Adaptations from the paper, stated explicitly rather than left implicit:

- **No positional encoding.** The paper adds sinusoidal position signals
  because word order carries meaning. Here, each token is a game object
  (a card in some zone) — the input is naturally a *set*, not a sequence;
  "first card in your hand" isn't meaningful the way "first word in the
  sentence" is. Self-attention is permutation-equivariant by construction,
  which is the right inductive bias for a set of cards, so positional
  encoding is deliberately omitted rather than forgotten. Each token
  still encodes *which zone and whose* it is (see `state_encoding.py`),
  which is the information positional encoding would otherwise need to
  stand in for.
- **Encoder only, no decoder.** The task is position evaluation (one
  state in, one scalar value out), not sequence generation — there's
  nothing to decode. This reuses the paper's encoder stack (self-attention
  sublayer + position-wise feed-forward sublayer, each wrapped in a
  residual connection and LayerNorm, exactly as Section 3.1 describes)
  and adds a pooling + value head on top instead of a decoder.
- **Much smaller**: this project's "vocabulary" is on the order of tens
  of card tokens, not tens of thousands of wordpieces, and training runs
  single-core CPU, not 8 GPUs for days. `d_model`, head count, and layer
  count are all small by design (see `ValueNetwork` defaults) — this is
  a first working version of the pipeline, not a claim that these
  hyperparameters are tuned.
- **No dropout/label smoothing.** The paper's regularization choices
  (5.4) matter at their data/compute scale; with a tiny model and tiny
  dataset here, they're not the limiting factor yet — see README for
  what is.

Everything in this file operates on a single example at a time (shape
`(seq_len, dim)` — no batch dimension), matching the autodiff engine's
scope.
"""
from __future__ import annotations

import numpy as np

from .autodiff import Tensor


class Linear:
    def __init__(self, in_dim: int, out_dim: int, rng: np.random.Generator):
        scale = (1.0 / in_dim) ** 0.5
        self.W = Tensor(rng.uniform(-scale, scale, (in_dim, out_dim)), requires_grad=True)
        self.b = Tensor(np.zeros((1, out_dim)), requires_grad=True)

    def __call__(self, x: Tensor) -> Tensor:
        return x.matmul(self.W) + self.b

    def params(self) -> list[Tensor]:
        return [self.W, self.b]


class LayerNorm:
    def __init__(self, dim: int, eps: float = 1e-5):
        self.gamma = Tensor(np.ones((1, dim)), requires_grad=True)
        self.beta = Tensor(np.zeros((1, dim)), requires_grad=True)
        self.eps = eps

    def __call__(self, x: Tensor) -> Tensor:
        mean = x.mean(axis=-1, keepdims=True)
        centered = x - mean
        var = (centered * centered).mean(axis=-1, keepdims=True)
        normed = centered * ((var + self.eps) ** -0.5)
        return normed * self.gamma + self.beta

    def params(self) -> list[Tensor]:
        return [self.gamma, self.beta]


class MultiHeadSelfAttention:
    """Section 3.2.2. Operates on x of shape (seq_len, dim)."""

    def __init__(self, dim: int, num_heads: int, rng: np.random.Generator):
        assert dim % num_heads == 0, "dim must be divisible by num_heads"
        self.dim = dim
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.wq = Linear(dim, dim, rng)
        self.wk = Linear(dim, dim, rng)
        self.wv = Linear(dim, dim, rng)
        self.wo = Linear(dim, dim, rng)

    def __call__(self, x: Tensor) -> Tensor:
        seq_len = x.shape[0]
        q, k, v = self.wq(x), self.wk(x), self.wv(x)

        def split_heads(t: Tensor) -> Tensor:
            # (seq_len, dim) -> (seq_len, heads, head_dim) -> (heads, seq_len, head_dim)
            return t.reshape(seq_len, self.num_heads, self.head_dim).transpose((1, 0, 2))

        qh, kh, vh = split_heads(q), split_heads(k), split_heads(v)
        scores = qh.matmul(kh.transpose((0, 2, 1))) * (1.0 / np.sqrt(self.head_dim))
        attn = scores.softmax(axis=-1)
        out = attn.matmul(vh)  # (heads, seq_len, head_dim)
        out = out.transpose((1, 0, 2)).reshape(seq_len, self.dim)
        return self.wo(out)

    def params(self) -> list[Tensor]:
        return self.wq.params() + self.wk.params() + self.wv.params() + self.wo.params()


class FeedForward:
    """Section 3.3: two linear layers with a ReLU in between."""

    def __init__(self, dim: int, hidden_dim: int, rng: np.random.Generator):
        self.l1 = Linear(dim, hidden_dim, rng)
        self.l2 = Linear(hidden_dim, dim, rng)

    def __call__(self, x: Tensor) -> Tensor:
        return self.l2(self.l1(x).relu())

    def params(self) -> list[Tensor]:
        return self.l1.params() + self.l2.params()


class EncoderLayer:
    """Section 3.1: self-attention sublayer + FFN sublayer, each wrapped
    as LayerNorm(x + Sublayer(x)) (post-norm, as in the original paper)."""

    def __init__(self, dim: int, num_heads: int, ff_hidden_dim: int, rng: np.random.Generator):
        self.attn = MultiHeadSelfAttention(dim, num_heads, rng)
        self.norm1 = LayerNorm(dim)
        self.ff = FeedForward(dim, ff_hidden_dim, rng)
        self.norm2 = LayerNorm(dim)

    def __call__(self, x: Tensor) -> Tensor:
        x = self.norm1(x + self.attn(x))
        x = self.norm2(x + self.ff(x))
        return x

    def params(self) -> list[Tensor]:
        return self.attn.params() + self.norm1.params() + self.ff.params() + self.norm2.params()


class ValueNetwork:
    """Token embeddings -> N encoder layers -> mean-pool -> small MLP ->
    tanh scalar in [-1, 1] (predicted win margin for the player to move,
    matching the label scheme in `train.py`)."""

    def __init__(self, input_dim: int, dim: int = 32, num_heads: int = 4,
                 num_layers: int = 2, ff_hidden_dim: int = 64, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.input_proj = Linear(input_dim, dim, rng)
        self.layers = [EncoderLayer(dim, num_heads, ff_hidden_dim, rng) for _ in range(num_layers)]
        self.head1 = Linear(dim, dim, rng)
        self.head2 = Linear(dim, 1, rng)

    def __call__(self, tokens: Tensor) -> Tensor:
        """tokens: Tensor of shape (num_tokens, input_dim). Returns a
        scalar Tensor (shape (1,1))."""
        x = self.input_proj(tokens)
        for layer in self.layers:
            x = layer(x)
        pooled = x.mean(axis=0, keepdims=True)  # (1, dim) — permutation-invariant pooling
        h = self.head1(pooled).relu()
        return self.head2(h).tanh()

    def params(self) -> list[Tensor]:
        p = self.input_proj.params() + self.head1.params() + self.head2.params()
        for layer in self.layers:
            p += layer.params()
        return p
