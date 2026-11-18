"""Reproduce a published MTEB figure through the space adapter, with the official evaluator.

    python evaluation/mteb_gate.py NAME --model-dir DIR [--device cuda] [--batch-size 64]

NAME selects a check of config/mteb-published.json. The check names a registered space whose
templates and token limit follow the configuration under which MTEB published the figure.
The gate passes when the measured score is within the declared tolerance, in points.
"""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys

import numpy as np

from search_retrieval.adapters.space_encoder import SpaceEncoder, render
from search_retrieval.experiment.run_config import read_space


ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "config/mteb-published.json"


def select(name: str) -> tuple[dict, float]:
    published = json.loads(PUBLISHED.read_bytes())
    matches = [check for check in published["checks"] if check["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one published check named {name}")
    return matches[0], published["tolerance_points"]


def judge(measured: float, published: float, tolerance_points: float) -> dict:
    """Scores are fractions; the tolerance is in points."""
    difference = (measured - published) * 100
    return {"measured": measured, "published": published, "difference_points": difference,
            "tolerance_points": tolerance_points, "passed": abs(difference) <= tolerance_points}


def gate_model(encoder, body: dict, name: str, batch_size: int):
    from mteb.models.abs_encoder import AbsEncoder
    from mteb.models.model_meta import ModelMeta, ScoringFunction
    from mteb.types import PromptType

    class GateModel(AbsEncoder):
        """MTEB hands corpus rows as `text` = title + space + body; queries as `text`."""

        def __init__(self):
            # The enum, not the string: mteb 2.27 selects the similarity by identity
            # (`is ScoringFunction.COSINE`) and create_empty does not convert overwrites.
            self.mteb_model_meta = ModelMeta.create_empty(overwrites={
                "name": f"local/{name}", "revision": body["revision"],
                "similarity_fn_name": ScoringFunction.COSINE,
            })

        def encode(self, inputs, *, task_metadata, hf_split, hf_subset, prompt_type=None, **kwargs):
            template = body["query_template"] if prompt_type == PromptType.query else body["doc_template"]
            rows = []
            for batch in inputs:
                texts = [render(template, "", text) for text in batch["text"]]
                for start in range(0, len(texts), batch_size):
                    rows.append(encoder.encode_texts(texts[start:start + batch_size]))
            return np.concatenate(rows)

    return GateModel()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    check, tolerance = select(args.name)
    body, space = read_space(ROOT, check["space"])
    encoder = SpaceEncoder(body, args.model_dir, device=args.device)

    import mteb

    task = mteb.get_task(check["task"])
    result = mteb.evaluate(gate_model(encoder, body, args.name, args.batch_size), [task],
                           cache=None, overwrite_strategy="always")
    rows = result.task_results[0].scores[check["split"]]
    if len(rows) != 1:
        raise ValueError("Expected one subset in the task result")
    outcome = {
        "check": check["name"], "task": check["task"], "split": check["split"], "measure": check["measure"],
        "space": space.digest, "space_manifest": check["space"], "model_revision": body["revision"],
        "published_model_revision": check["published_model_revision"],
        "published_mteb_version": check["published_mteb_version"], "mteb_version": version("mteb"),
        "device": args.device, **judge(float(rows[0][check["measure"]]), check["published"], tolerance),
    }
    print(json.dumps(outcome, sort_keys=True))
    return int(not outcome["passed"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"ValueError: {error}", file=sys.stderr)
        raise SystemExit(1) from error
