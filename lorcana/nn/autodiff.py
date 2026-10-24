"""A minimal reverse-mode autodiff engine over NumPy arrays.

No PyTorch/JAX available in this environment (PyTorch's default wheel
hard-requires CUDA shared libraries even for CPU-only use, and the actual
CPU-only build is hosted on download.pytorch.org, which isn't reachable
from this sandbox — see the project history for the dead end). This is
small enough to verify correctly by hand via numerical gradient checking
(see `lorcana/nn/test_autodiff.py`) rather than trusting it blind.

Design: `Tensor` wraps a NumPy array and, when `requires_grad=True`,
records the operation that produced it (`_prev`, the parent Tensors, and
`_backward`, a closure that accumulates this op's local gradient into
each parent's `.grad`). `.backward()` does a topological sort from the
output and calls each node's `_backward` in reverse order — standard
reverse-mode autodiff, same idea as PyTorch's autograd, just far smaller
and scoped to exactly the ops the Transformer encoder needs.

Every example in this project processes ONE game state (one token
sequence) at a time rather than batching — simpler to get right, and the
dataset/model here are both small enough that per-example training is
fine on CPU.
"""
from __future__ import annotations

import numpy as np


def _unbroadcast(grad: np.ndarray, shape: tuple) -> np.ndarray:
    """Sums `grad` down to `shape`, undoing whatever broadcasting happened
    on the forward pass (standard requirement for correct broadcasted-op
    gradients)."""
    while grad.ndim > len(shape):
        grad = grad.sum(axis=0)
    for i, dim in enumerate(shape):
        if dim == 1 and grad.shape[i] != 1:
            grad = grad.sum(axis=i, keepdims=True)
    return grad


class Tensor:
    def __init__(self, data, requires_grad: bool = False, _prev: tuple = (), _backward=None):
        self.data = np.asarray(data, dtype=np.float64)
        self.requires_grad = requires_grad
        self.grad: np.ndarray | None = None
        self._prev = _prev
        self._backward = _backward or (lambda: None)

    @property
    def shape(self):
        return self.data.shape

    def zero_grad(self):
        self.grad = None

    # --- graph construction helpers ----------------------------------
    def _mk(self, data, parents, backward_fn) -> "Tensor":
        needs_grad = self.requires_grad or any(p.requires_grad for p in parents if isinstance(p, Tensor))
        return Tensor(data, requires_grad=needs_grad, _prev=parents, _backward=backward_fn)

    # --- core ops: each defines forward (above) + local backward (below) ---
    def __add__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        out = self._mk(self.data + other.data, (self, other), None)

        def backward():
            if self.requires_grad:
                g = _unbroadcast(out.grad, self.data.shape)
                self.grad = g if self.grad is None else self.grad + g
            if other.requires_grad:
                g = _unbroadcast(out.grad, other.data.shape)
                other.grad = g if other.grad is None else other.grad + g
        out._backward = backward
        return out

    def __neg__(self):
        return self * -1.0

    def __sub__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        return self + (-other)

    def __mul__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        out = self._mk(self.data * other.data, (self, other), None)

        def backward():
            if self.requires_grad:
                g = _unbroadcast(out.grad * other.data, self.data.shape)
                self.grad = g if self.grad is None else self.grad + g
            if other.requires_grad:
                g = _unbroadcast(out.grad * self.data, other.data.shape)
                other.grad = g if other.grad is None else other.grad + g
        out._backward = backward
        return out

    __rmul__ = __mul__

    def __truediv__(self, other):
        other = other if isinstance(other, Tensor) else Tensor(other)
        return self * (other ** -1.0)

    def __pow__(self, p: float):
        out = self._mk(self.data ** p, (self,), None)

        def backward():
            if self.requires_grad:
                g = out.grad * p * (self.data ** (p - 1))
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    def matmul(self, other: "Tensor") -> "Tensor":
        out = self._mk(self.data @ other.data, (self, other), None)

        def backward():
            if self.requires_grad:
                g = out.grad @ other.data.swapaxes(-1, -2)
                self.grad = g if self.grad is None else self.grad + g
            if other.requires_grad:
                g = self.data.swapaxes(-1, -2) @ out.grad
                other.grad = g if other.grad is None else other.grad + g
        out._backward = backward
        return out

    def transpose(self, axes=None):
        axes = axes or tuple(reversed(range(self.data.ndim)))
        out = self._mk(self.data.transpose(axes), (self,), None)
        inv_axes = np.argsort(axes)

        def backward():
            if self.requires_grad:
                g = out.grad.transpose(inv_axes)
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    @property
    def T(self):
        return self.transpose()

    def reshape(self, *shape):
        out = self._mk(self.data.reshape(*shape), (self,), None)

        def backward():
            if self.requires_grad:
                g = out.grad.reshape(self.data.shape)
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    def sum(self, axis=None, keepdims=False):
        out = self._mk(self.data.sum(axis=axis, keepdims=keepdims), (self,), None)

        def backward():
            if self.requires_grad:
                g = out.grad
                if axis is not None and not keepdims:
                    g = np.expand_dims(g, axis)
                g = np.broadcast_to(g, self.data.shape).copy()
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    def mean(self, axis=None, keepdims=False):
        n = self.data.size if axis is None else self.data.shape[axis]
        return self.sum(axis=axis, keepdims=keepdims) * (1.0 / n)

    def relu(self):
        out = self._mk(np.maximum(self.data, 0), (self,), None)

        def backward():
            if self.requires_grad:
                g = out.grad * (self.data > 0)
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    def tanh(self):
        t = np.tanh(self.data)
        out = self._mk(t, (self,), None)

        def backward():
            if self.requires_grad:
                g = out.grad * (1 - t ** 2)
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    def softmax(self, axis=-1):
        shifted = self.data - np.max(self.data, axis=axis, keepdims=True)
        exp = np.exp(shifted)
        probs = exp / np.sum(exp, axis=axis, keepdims=True)
        out = self._mk(probs, (self,), None)

        def backward():
            if self.requires_grad:
                # Standard softmax-Jacobian-vector-product: dx = p * (dy - sum(dy*p, axis))
                dy = out.grad
                dot = np.sum(dy * probs, axis=axis, keepdims=True)
                g = probs * (dy - dot)
                self.grad = g if self.grad is None else self.grad + g
        out._backward = backward
        return out

    # --- graph traversal ------------------------------------------------
    def backward(self):
        topo: list[Tensor] = []
        visited = set()

        def build(t: "Tensor"):
            if id(t) in visited:
                return
            visited.add(id(t))
            for p in t._prev:
                if isinstance(p, Tensor):
                    build(p)
            topo.append(t)

        build(self)
        self.grad = np.ones_like(self.data)
        for t in reversed(topo):
            t._backward()

    def __repr__(self):
        return f"Tensor(shape={self.data.shape}, requires_grad={self.requires_grad})"
