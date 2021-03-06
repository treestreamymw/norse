from typing import Optional, Tuple

import numpy as np
import torch

from ..functional.lif import (
    LIFState,
    LIFFeedForwardState,
    LIFParameters,
    lif_step,
    lif_feed_forward_step,
)


class LIFCell(torch.nn.Module):
    """Module that computes a single euler-integration step of a
    LIF neuron-model with recurrence.
    More specifically it implements one integration step
    of the following ODE

    .. math::
        \begin{align*}
            \dot{v} &= 1/\tau_{\text{mem}} (v_{\text{leak}} - v + i) \\
            \dot{i} &= -1/\tau_{\text{syn}} i
        \end{align*}

    together with the jump condition

    .. math::
        z = \Theta(v - v_{\\text{th}})

    and transition equations

    .. math::
        \\begin{align*}
            v &= (1-z) v + z v_{\\text{reset}} \\
            i &= i + w_{\\text{input}} z_{\text{in}} \\
            i &= i + w_{\\text{rec}} z_{\text{rec}}
        \end{align*}

    where :math:`z_{\\text{rec}}` and :math:`z_{\\text{in}}` are the
    recurrent and input spikes respectively.

    Parameters:
        input_size (int): Size of the input.
        hidden_size (int): Size of the hidden state.
        p (LIFParameters): Parameters of the LIF neuron model.
        dt (float): Time step to use.

    Examples:

        >>> batch_size = 16
        >>> lif = LIFCell(10, 20)
        >>> input = torch.randn(batch_size, 10)
        >>> output, s0 = lif(input)
    """

    def __init__(
        self,
        input_size,
        hidden_size,
        p: LIFParameters = LIFParameters(),
        dt: float = 0.001,
    ):
        super(LIFCell, self).__init__()
        self.input_weights = torch.nn.Parameter(
            torch.randn(hidden_size, input_size) * np.sqrt(2 / hidden_size)
        )
        self.recurrent_weights = torch.nn.Parameter(
            torch.randn(hidden_size, hidden_size) * np.sqrt(2 / hidden_size)
        )
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.p = p
        self.dt = dt

    def extra_repr(self):
        s = f"{self.input_size}, {self.hidden_size}, p={self.p}, dt={self.dt}"
        return s

    def forward(
        self, input_tensor: torch.Tensor, state: Optional[LIFState] = None
    ) -> Tuple[torch.Tensor, LIFState]:
        if state is None:
            state = LIFState(
                z=torch.zeros(
                    input_tensor.shape[0],
                    self.hidden_size,
                    device=input_tensor.device,
                    dtype=input_tensor.dtype,
                ),
                v=self.p.v_leak,
                i=torch.zeros(
                    input_tensor.shape[0],
                    self.hidden_size,
                    device=input_tensor.device,
                    dtype=input_tensor.dtype,
                ),
            )
        return lif_step(
            input_tensor,
            state,
            self.input_weights,
            self.recurrent_weights,
            p=self.p,
            dt=self.dt,
        )


class LIFLayer(torch.nn.Module):
    """
    A neuron layer that wraps a recurrent LIFCell in time such
    that the layer keeps track of temporal sequences of spikes.
    After application, the layer returns a tuple containing
      (spikes from all timesteps, state from the last timestep).

    Example:
    >>> data = torch.zeros(10, 5, 2) # 10 timesteps, 5 batches, 2 neurons
    >>> l = LIFLayer(2, 4)
    >>> l(data) # Returns tuple of (Tensor(10, 5, 4), LIFState)
    """

    def __init__(self, *cell_args, **kw_args):
        super(LIFLayer, self).__init__()
        self.cell = LIFCell(*cell_args, **kw_args)

    def forward(
        self, input_tensor: torch.Tensor, state: Optional[LIFState] = None
    ) -> Tuple[torch.Tensor, LIFState]:
        inputs = input_tensor.unbind(0)
        outputs = []  # torch.jit.annotate(List[torch.Tensor], [])
        for _, input_step in enumerate(inputs):
            out, state = self.cell(input_step, state)
            outputs += [out]
        return torch.stack(outputs), state


class LIFFeedForwardCell(torch.nn.Module):
    """Module that computes a single euler-integration step of a LIF neuron.
    It takes as input the input current as generated by an arbitrary torch
    module or function. More specifically it implements one integration step
    of the following ODE

    .. math::
        \\begin{align*}
            \dot{v} &= 1/\\tau_{\\text{mem}} (v_{\\text{leak}} - v + i) \\\\
            \dot{i} &= -1/\\tau_{\\text{syn}} i
        \end{align*}

    together with the jump condition

    .. math::
        z = \Theta(v - v_{\\text{th}})

    and transition equations

    .. math::
        i = i + i_{\\text{in}}

    where :math:`i_{\\text{in}}` is meant to be the result of applying
    an arbitrary pytorch module (such as a convolution) to input spikes.

    Parameters:
        p (LIFParameters): Parameters of the LIF neuron model.
        dt (float): Time step to use.

    Examples:

        >>> batch_size = 16
        >>> lif = LIFFeedForwardCell()
        >>> data = torch.randn(batch_size, 20, 30)
        >>> output, s0 = lif(data)
    """

    def __init__(self, p: LIFParameters = LIFParameters(), dt: float = 0.001):
        super(LIFFeedForwardCell, self).__init__()
        self.p = p
        self.dt = dt

    def extra_repr(self):
        s = f"p={self.p}, dt={self.dt}"
        return s

    def forward(
        self, x: torch.Tensor, state: Optional[LIFFeedForwardState] = None
    ) -> Tuple[torch.Tensor, LIFFeedForwardState]:
        if state is None:
            state = LIFFeedForwardState(
                v=self.p.v_leak,
                i=torch.zeros(x.shape, device=x.device, dtype=x.dtype),
            )
        return lif_feed_forward_step(x, state, p=self.p, dt=self.dt)


class LIFFeedForwardLayer(torch.nn.Module):
    """
    A neuron layer that wraps a recurrent LIFCell in time such
    that the layer keeps track of temporal sequences of spikes.
    After application, the layer returns a tuple containing
      (spikes from all timesteps, state from the last timestep).

    Example:
    >>> data = torch.zeros(10, 5, 2) # 10 timesteps, 5 batches, 2 neurons
    >>> l = LIFLayer(2, 4)
    >>> l(data) # Returns tuple of (Tensor(10, 5, 4), LIFState)
    """

    def __init__(self, *cell_args, **kw_args):
        super(LIFFeedForwardLayer, self).__init__()
        self.cell = LIFFeedForwardCell(*cell_args, **kw_args)

    def forward(
        self, input_tensor: torch.Tensor, state: Optional[LIFFeedForwardState] = None
    ) -> Tuple[torch.Tensor, LIFFeedForwardState]:
        inputs = input_tensor.unbind(0)
        outputs = []  # torch.jit.annotate(List[torch.Tensor], [])
        for _, input_step in enumerate(inputs):
            out, state = self.cell(input_step, state)
            outputs += [out]
        return torch.stack(outputs), state
