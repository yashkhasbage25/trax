# coding=utf-8
# Copyright 2021 The Trax Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Tests for Funnel-Transformer models."""

from absl.testing import absltest
from absl.testing import parameterized
import jax
import numpy as np
from trax import fastmath
from trax import layers as tl
from trax import shapes
import trax.models.research.funnel_transformer as ft


class FunnelTransformerTest(parameterized.TestCase):

  def test_mean_pool(self):
    x = np.ones((1, 4, 1))
    x[0, :3, 0] = [5., 2., 4.]

    pooling = ft.PoolLayer(tl.AvgPool, (2,), (2,))
    y = pooling(x)

    self.assertEqual(y.shape, (1, 2, 1))
    self.assertEqual(y.tolist(), [[[5.], [3.]]])

  def test_mask_pool(self):
    x = np.array([1, 0, 0, 1], dtype=bool).reshape((1, 1, 1, 4))
    pooling_cls = ft.MaskPool((2,), (2,))
    y1 = pooling_cls(x)

    self.assertEqual(y1.shape, (1, 1, 1, 2))
    self.assertEqual(y1.squeeze().tolist(), [True, False])

    pooling_without_cls = ft.MaskPool((2,), (2,), separate_cls=False)
    y2 = pooling_without_cls(x)

    self.assertEqual(y2.shape, (1, 1, 1, 2))
    self.assertEqual(y2.squeeze().tolist(), [True, True])

  def test_upsampler(self):
    long = np.ones((1, 8, 1))
    short = np.ones((1, 2, 1))
    total_pool_size = long.shape[1] // short.shape[1]
    up_cls = ft._Upsampler(total_pool_size, separate_cls=True)
    up = ft._Upsampler(total_pool_size, separate_cls=False)

    y_cls = up_cls([short, long])
    y = up((short, long))
    self.assertEqual(y_cls.shape, long.shape)
    self.assertEqual(y.shape, long.shape)

    self.assertEqual(y_cls.squeeze().tolist(), 5*[2] + 3*[1])
    self.assertEqual(y.squeeze().tolist(), 8*[2])

  def test_funnel_block_forward_shape(self):
    n_even = 4
    d_model = 8

    x = np.ones((1, n_even, d_model), dtype=np.float)
    mask = np.ones((1, n_even), dtype=np.int32)

    masker = tl.PaddingMask()
    mask = masker(mask)

    block = tl.Serial(
        ft._FunnelBlock(d_model, 8, 2, 0.1, None, 'train', tl.Relu,
                        tl.AvgPool, (2,), (2,), separate_cls=True))

    xs = [x, mask]
    _, _ = block.init(shapes.signature(xs))

    y, _ = block(xs)

    self.assertEqual(y.shape, (1, n_even // 2, d_model))

  def test_funnel_transformer_encoder_forward_shape(self):
    n_classes = 5
    model = ft.FunnelTransformerEncoder(2, n_classes=n_classes, d_model=8,
                                        d_ff=8, encoder_segment_lengths=(1, 1),
                                        n_heads=2, max_len=8)

    batch_size = 2
    n_tokens = 4
    x = np.ones((batch_size, n_tokens), dtype=np.int32)
    _ = model.init(shapes.signature(x))
    y = model(x)

    self.assertEqual(y.shape, (batch_size, n_classes))

  def test_funnel_transformer_forward_shape(self):
    d_model = 8
    vocab_size = 7
    model = ft.FunnelTransformer(7, d_model=d_model, d_ff=8,
                                 encoder_segment_lengths=(1, 1),
                                 n_decoder_blocks=1, n_heads=2, max_len=8)

    batch_size = 2
    n_tokens = 4
    x = np.ones((batch_size, n_tokens), dtype=np.int32)
    _ = model.init(shapes.signature(x))
    y = model(x)

    self.assertEqual(y.shape, (batch_size, n_tokens, vocab_size))

  def test_funnel_transformer_lm_forward_shape(self):
    d_model = 8
    vocab_size = 7
    x = np.ones((3, 6)).astype(np.int32)

    simple_funnel = ft.RelformerLM(
        vocab_size,
        shorten_factor=3,
        n_rel_layers=1,
        vanilla_layers=(1, 1),
        d_model=d_model, d_ff=d_model, n_heads=2,
        vanilla_attn_type=tl.SelfAttention
    )
    _, _ = simple_funnel.init(shapes.signature(x))
    y = simple_funnel(x)
    self.assertEqual(y.shape, (3, 6, vocab_size))

    multi_stage_funnel = ft.FunnelTransformerLM(
        vocab_size,
        shorten_factors=(3, 2),
        n_funnel_blocks=(0, 0),
        vanilla_layers=(0, 0),
        d_model=d_model, d_ff=d_model, n_heads=2)

    _, _ = multi_stage_funnel.init(shapes.signature(x))
    y = multi_stage_funnel(x)
    self.assertEqual(y.shape, (3, 6, vocab_size))

  def test_funnel_transformer_lm_autoregressive_property(self):
    input_shape = (1, 12)
    d_model = 8
    vocab_size = 26
    rng_1 = jax.random.PRNGKey(0)
    rng_2 = jax.random.PRNGKey(1)

    def _get_output_logits(unitialized_eval_model: tl.Layer, x):
      input_signature = shapes.signature(x)
      unitialized_eval_model.init(input_signature, rng=rng_1, use_cache=False)

      output_logits, *_ = unitialized_eval_model(x, rng=rng_1)
      return output_logits

    with fastmath.use_backend(fastmath.Backend.JAX):
      model = ft.RelformerLM(
          vocab_size,
          shorten_factor=3,
          n_rel_layers=1,
          vanilla_layers=(1, 1),
          d_model=d_model, d_ff=4*d_model, n_heads=2,
          vanilla_attn_type=tl.SelfAttention
      )

      x_1 = jax.random.randint(rng_1, input_shape, 0, vocab_size)
      y_1 = _get_output_logits(model, x_1)

      x_2 = jax.random.randint(rng_2, input_shape, 0, vocab_size)

      for i in range(input_shape[1]):
        masked_x_2 = np.concatenate((x_1[:, :i], x_2[:, i:]), axis=1)

        y_2 = _get_output_logits(model, masked_x_2)
        self.assertEqual(y_2.shape[0], input_shape[1])
        np.testing.assert_array_almost_equal(y_1[:i+1], y_2[:i+1])


if __name__ == '__main__':
  absltest.main()
