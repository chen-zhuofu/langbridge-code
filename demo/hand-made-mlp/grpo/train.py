import json
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, PreTrainedTokenizerBase, PreTrainedModel
# PreTrainedTokenizerBase, PreTrainedModel
from vllm import LLM, SamplingParams
import reward_fn
import random
from typing import Callable

def sample_batch(data_path: str, data_size: int) -> tuple[list[str], list[str]]:
    all_samples = []
    with open(data_path, 'r') as f:
        for line in f:
            all_samples.append(json.loads(line))
    samples = random.sample(all_samples, data_size)
    template_path = "/practice.prompt"
    with open(template_path, 'r') as f:
        template = f.read()
    prompts = []
    responses = []
    for sample in samples:
        prompts.append(template.format(question=sample["question"]))
        responses.append(sample["answers"])
    return prompts, responses
    

def tokenize_prompt_and_output(
    prompts: list[str], 
    responses: list[str], 
    tokenizer: PreTrainedTokenizerBase
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    max_len = 0
    mask_positions = []
    concats = []
    # first loop find mask position and max of concat, and concat themselves ofc
    for prompt, response in zip(prompts, responses):
        encoded_p = tokenizer.encode(prompt)
        encoded_r = tokenizer.encode(response)
        concat = encoded_p + encoded_r
        concats.append(concat)
        mask_positions.append(len(encoded_p))
        max_len = max(max_len, len(concat))
    # second loop return a list of mask, list of concat with padding(=> inputs, labels)
    inputs, labels, masks = [], [], []
    for mask_pos, concat in zip(mask_positions, concats):
        padding_len = max_len - len(concat)
        real_concat = concat + [tokenizer.pad_token_id] * padding_len
        inputs.append(real_concat[:-1])
        labels.append(real_concat[1:])
        mask = [False] * (mask_pos-1) + [True] * (len(concat) - mask_pos) + [False] * padding_len
        masks.append(mask)
    return (
        torch.tensor(inputs, dtype=torch.long),
        torch.tensor(labels, dtype=torch.long),
        torch.tensor(masks, dtype=torch.bool),
    )

def get_response_log_probs(
    model:PreTrainedModel, 
    inputs: torch.Tensor, 
    labels: torch.Tensor
) -> torch.Tensor:
    logits = model(inputs).logits
    all_log_probs = torch.log_softmax(logits, dim=-1)
    return all_log_probs.gather(-1, labels.unsqueeze(-1)).squeeze(-1)

def compute_group_normalized_rewards(
    reward_fn: Callable, 
    ground_truths: list[str], 
    rollouts: list[str],
    group_size: int
) -> tuple[torch.Tensor, torch.Tensor]:
    rewards = []
    for ground_truth, rollout in zip(ground_truths, rollouts):
        rewards.append(reward_fn(ground_truth, rollout)["reward"])
    # we already have the raw rewards, then calculate advantage, advantage with std
    rewards = torch.tensor(rewards, dtype=torch.float32)
    grouped_rewards = rewards.reshape(-1, group_size)
    mean = grouped_rewards.mean(-1, keepdim=True)
    advantages = grouped_rewards - mean
    return grouped_rewards.reshape(-1), advantages.reshape(-1)
    # ================================
    # std = rewards.std(-1, keepdim=True)
    # return rewards.reshape(-1), (rewards - mean / std).reshape(-1)
    # ================================

def compute_pg_loss(
    reward_or_advantage : torch.Tensor, 
    log_probs : torch.Tensor,
    masks : torch.Tensor,
    old_log_probs : torch.Tensor,
    cliprange: float
) -> torch.Tensor:
    reward_or_advantage = reward_or_advantage.unsqueeze(-1)
    # calculate baseline(rewards)
    # calculate reinforce_baseline(advantage)
    # =======================================================
    # per_token_loss = -log_probs * reward_or_advantage
    
    # calculate grpo(clip): advantage * min(clip, ratio)
    # =======================================================
    ratio = torch.exp(log_probs - old_log_probs)
    clip = torch.clamp(ratio, 1 - cliprange, 1 + cliprange)
    per_token_loss = -torch.min(ratio * reward_or_advantage, clip * reward_or_advantage)
    
    masked_per_token_loss = per_token_loss.masked_fill(masks==False, 0)
    
    # masked_mean
    # =======================================================
    line_mean = masked_per_token_loss.sum(-1, keepdim=True) / masks.sum(-1, keepdim=True)
    return line_mean.mean()
    # =======================================================
    
    # masked_normalize
    # =======================================================
    # return masked_per_token_loss.mean()
    # =======================================================

def main():
    # load config
    with open("/config", 'r') as f:
        config = json.load(f)
    data_path = config["data_path"]
    model_path = config["model_path"]
    rollout_batch_size = config["rollout_batch_size"]
    train_batch_size = config["train_batch_size"]
    group_size = config["group_size"]
    temperature = config["temperature"]
    cliprange = config["cliprange"]
    # prepare model and tokenizer
    model = AutoModelForCausalLM.from_pretrained(model_path)
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    rollout_model = LLM(model_path)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config["lr"],
        weight_decay=config["weight_decay"],
        betas=(config["beta1"], config["beta2"]),
    )
    model.train()
    while True:
        # prepare data
        prompts, responses = sample_batch(data_path, rollout_batch_size // group_size)
        repeated_prompts = [p for p in prompts for _ in range(group_size)]
        repeated_responses = [p for p in responses for _ in range(group_size)]
        # rollout 
        sampling_params = SamplingParams(temperature=temperature, n=1)
        rollout_outputs = rollout_model.generate(repeated_prompts, sampling_params)
        rollout_responses = [output.outputs[0].text for output in rollout_outputs]
        # organize it to be input and labels
        inputs, labels, response_mask = tokenize_prompt_and_output(repeated_prompts, rollout_responses, tokenizer)
        # calculate old response log probs
        with torch.no_grad():
            old_log_probs = get_response_log_probs(model, inputs, labels)
        # for each rollout batch, we train several steps
        for step in range(rollout_batch_size // train_batch_size):
            start = step * train_batch_size
            end = start + 1 * train_batch_size
            step_old_log_probs = old_log_probs[start : end]
            step_responses = repeated_responses[start : end]
            step_rollout_responses = rollout_responses[start : end]
            step_inputs = inputs[start : end]
            step_labels = labels[start : end]
            step_response_mask = response_mask[start : end]
            # calculate log_probs
            log_probs = get_response_log_probs(model, step_inputs, step_labels)
            # calculate rewards and advantage 
            rewards, advantages = compute_group_normalized_rewards(reward_fn, step_responses, step_rollout_responses, group_size)
            loss = compute_pg_loss(advantages, log_probs, step_response_mask, step_old_log_probs, cliprange)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)

if __name__ == "__main__":
    main()