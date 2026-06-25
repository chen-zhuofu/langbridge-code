import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


try:
    import torch
except ModuleNotFoundError:  # pragma: no cover - depends on optional training env
    torch = None


TRAIN_PATH = Path(__file__).resolve().parents[1] / "train.py"


def load_train_module():
    if torch is None:
        raise unittest.SkipTest("torch is required for grpo train unit tests")

    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = object
    transformers.AutoTokenizer = object
    transformers.PreTrainedTokenizerBase = object
    transformers.PreTrainedModel = object

    vllm = types.ModuleType("vllm")
    vllm.LLM = object
    vllm.SamplingParams = object

    reward_fn = types.ModuleType("reward_fn")

    module_name = "grpo_train_under_test"
    original_modules = {
        name: sys.modules.get(name) for name in ("transformers", "vllm", "reward_fn")
    }

    sys.modules["transformers"] = transformers
    sys.modules["vllm"] = vllm
    sys.modules["reward_fn"] = reward_fn
    sys.modules.pop(module_name, None)

    try:
        spec = importlib.util.spec_from_file_location(module_name, TRAIN_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class FakeTokenizer:
    pad_token_id = 0

    def __init__(self, mapping):
        self.mapping = mapping

    def encode(self, text):
        return list(self.mapping[text])


class FakeModel:
    def __init__(self, logits):
        self.logits = logits

    def __call__(self, inputs):
        return types.SimpleNamespace(logits=self.logits)


class TrainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.train = load_train_module()

    def test_sample_batch_formats_selected_questions_with_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir) / "data.jsonl"
            template_path = Path(tmpdir) / "practice.prompt"
            records = [
                {"question": "2 + 2?", "answers": "4"},
                {"question": "3 + 5?", "answers": "8"},
            ]
            data_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            template_path.write_text("Solve: {question}", encoding="utf-8")

            real_open = open

            def redirected_open(path, *args, **kwargs):
                if path == "/practice.prompt":
                    path = template_path
                return real_open(path, *args, **kwargs)

            with (
                mock.patch("builtins.open", side_effect=redirected_open),
                mock.patch.object(self.train.random, "sample", return_value=records),
            ):
                prompts, responses = self.train.sample_batch(str(data_path), data_size=2)

        self.assertEqual(prompts, ["Solve: 2 + 2?", "Solve: 3 + 5?"])
        self.assertEqual(responses, ["4", "8"])

    def test_tokenize_prompt_and_output_pads_labels_and_masks_response_tokens(self):
        tokenizer = FakeTokenizer(
            {
                "p1": [11, 12],
                "r1": [21, 22, 23],
                "p2": [13],
                "r2": [24],
            }
        )

        inputs, labels, masks = self.train.tokenize_prompt_and_output(
            ["p1", "p2"],
            ["r1", "r2"],
            tokenizer,
        )

        self.assertTrue(
            torch.equal(inputs, torch.tensor([[11, 12, 21, 22], [13, 24, 0, 0]]))
        )
        self.assertTrue(
            torch.equal(labels, torch.tensor([[12, 21, 22, 23], [24, 0, 0, 0]]))
        )
        self.assertTrue(
            torch.equal(
                masks,
                torch.tensor(
                    [
                        [False, True, True, True],
                        [True, False, False, False],
                    ],
                    dtype=torch.bool,
                ),
            )
        )

    def test_get_response_log_probs_gathers_label_probabilities(self):
        logits = torch.tensor(
            [
                [[2.0, 0.0, 1.0], [0.0, 3.0, 1.0]],
                [[1.0, 1.0, 2.0], [4.0, 0.0, 0.0]],
            ]
        )
        labels = torch.tensor([[0, 2], [2, 1]])
        model = FakeModel(logits)

        actual = self.train.get_response_log_probs(model, torch.zeros((2, 2)), labels)
        expected = torch.log_softmax(logits, dim=-1).gather(
            -1, labels.unsqueeze(-1)
        ).squeeze(-1)

        self.assertTrue(torch.allclose(actual, expected))

    def test_compute_group_normalized_rewards_returns_raw_rewards_and_advantages(self):
        def reward_function(ground_truth, rollout):
            return {"reward": len(set(ground_truth) & set(rollout))}

        rewards, advantages = self.train.compute_group_normalized_rewards(
            reward_function,
            ["abc", "abc", "xy", "xy"],
            ["ab", "c", "x", "yz"],
            group_size=2,
        )

        self.assertTrue(torch.equal(rewards, torch.tensor([2.0, 1.0, 1.0, 1.0])))
        self.assertTrue(torch.equal(advantages, torch.tensor([0.5, -0.5, 0.0, 0.0])))

    def test_compute_group_normalized_rewards_has_zero_mean_within_each_group(self):
        def reward_function(_ground_truth, rollout):
            return {"reward": len(rollout)}

        rewards, advantages = self.train.compute_group_normalized_rewards(
            reward_function,
            ["a", "b", "c", "d"],
            ["x", "yy", "zzz", "w"],
            group_size=2,
        )

        self.assertTrue(torch.equal(rewards, torch.tensor([1.0, 2.0, 3.0, 1.0])))
        self.assertAlmostEqual(advantages[:2].sum().item(), 0.0)
        self.assertAlmostEqual(advantages[2:].sum().item(), 0.0)

    def test_compute_pg_loss_uses_clipped_ratio_and_masked_token_mean(self):
        advantages = torch.tensor([2.0, -1.0])
        log_probs = torch.log(torch.tensor([[1.3, 0.7, 1.0], [0.6, 1.2, 1.0]]))
        old_log_probs = torch.zeros_like(log_probs)
        masks = torch.tensor(
            [[True, True, False], [True, False, True]],
            dtype=torch.bool,
        )

        loss = self.train.compute_pg_loss(
            advantages,
            log_probs,
            masks,
            old_log_probs,
            cliprange=0.2,
        )

        self.assertTrue(torch.allclose(loss, torch.tensor(-0.5)))


if __name__ == "__main__":
    unittest.main()
